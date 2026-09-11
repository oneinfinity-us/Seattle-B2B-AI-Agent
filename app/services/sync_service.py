"""
The same triage/draft/persist logic app/api/routes.py's sync_inbox exposes over SSE for a live
"Check inbox now" click, but for a background run with no one watching. Deliberately not shared as one
generator with the SSE route: that route yields a delta per streamed token specifically for the live
UX, which a fire-and-forget background job over every tenant has no use for — forcing both through one
abstraction would cost more complexity than the small amount of duplicated loop code it'd save.

The notification-sending piece is shared (send_digest_notification), since two independent copies of
"who does the digest email go to" is exactly how that recipient bug happened in the first place.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.agents.assistant_agent import ActionContext, AssistantWorkflow
from app.db.credential_repository import CredentialRepository
from app.db.repository import repository_session
from app.integrations.provider_factory import build_providers_for_tenant
from app.models.schemas import NotificationRequest, NotifyChannel

logger = structlog.get_logger()


async def send_digest_notification(app_state: Any, tenant_id: str, actions_created: int) -> None:
    if not actions_created:
        return
    # tenant_id *is* the connected Google account's email address (see app/api/auth_routes.py) — no
    # separate lookup needed, and no placeholder to forget to replace.
    await app_state.notifier.send(
        NotificationRequest(
            tenant_id=tenant_id,
            channel=NotifyChannel.EMAIL,
            recipient=tenant_id,
            subject=f"{actions_created} item(s) awaiting your review",
            body="Log in to review AI-drafted replies awaiting your approval.",
            idempotency_key=f"{tenant_id}:{datetime.now(timezone.utc).date()}:digest",
        )
    )


async def sync_tenant_inbox(app_state: Any, tenant_id: str, *, auto_notify_manager: bool = True) -> int:
    """Runs one sync cycle for one tenant, end to end, with no streaming. Returns the number of new
    actions created."""
    limiter_result = await app_state.rate_limiter.acquire(tenant_id)
    if not limiter_result.allowed:
        logger.info("sync_skipped_rate_limited", tenant_id=tenant_id)
        return 0

    email_provider, calendar_provider = await build_providers_for_tenant(app_state, tenant_id)
    workflow = AssistantWorkflow(app_state.llm_client, calendar_provider)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    messages = await email_provider.list_recent_messages(since)

    actions_created = 0
    async with repository_session(app_state.db_sessionmaker, email_provider) as repo:
        for message in messages:
            ctx = ActionContext(tenant_id=tenant_id, message=message)
            persisted = False

            async for ctx_snapshot, chunk in workflow.run(ctx):
                if ctx_snapshot.kind is None:
                    break  # skipped (e.g. an automated sender) - nothing to persist

                if not persisted:
                    await repo.create(
                        action_id=ctx_snapshot.action_id,
                        tenant_id=ctx_snapshot.tenant_id,
                        kind=ctx_snapshot.kind,
                        source_message_id=message.message_id,
                        reply_to=message.sender,
                        summary=f"{ctx_snapshot.kind.value.replace('_', ' ').title()}: {message.subject}",
                    )
                    persisted = True

                if not chunk:
                    await repo.update_state(
                        ctx_snapshot.action_id,
                        state=ctx_snapshot.state,
                        draft_content=ctx_snapshot.draft_content,
                        input_tokens=ctx_snapshot.input_tokens,
                        output_tokens=ctx_snapshot.output_tokens,
                        estimated_cost_usd=ctx_snapshot.estimated_cost_usd,
                    )

            if persisted:
                actions_created += 1

    if auto_notify_manager:
        await send_digest_notification(app_state, tenant_id, actions_created)

    return actions_created


async def sync_all_tenants(app_state: Any) -> None:
    """Runs sync_tenant_inbox for every onboarded tenant. Called on a schedule (see app/main.py)."""
    async with app_state.db_sessionmaker() as session:
        tenant_ids = await CredentialRepository(session).list_all_tenant_ids()

    for tenant_id in tenant_ids:
        try:
            created = await sync_tenant_inbox(app_state, tenant_id)
            logger.info("scheduled_sync_completed", tenant_id=tenant_id, actions_created=created)
        except Exception:  # noqa: BLE001 - one tenant's failure (e.g. a revoked Google grant) must not stop the rest
            logger.exception("scheduled_sync_failed", tenant_id=tenant_id)
