from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agents.assistant_agent import ActionContext, AssistantWorkflow
from app.core.sessions import require_tenant
from app.db.repository import ActionNotFoundError, InvalidDecisionError, repository_session
from app.integrations.provider_factory import build_providers_for_tenant
from app.models.schemas import (
    ActionDecisionRequest,
    NotificationRequest,
    NotifyChannel,
    PendingActionResponse,
    SyncRequest,
)

router = APIRouter()


@router.post("/assistant/sync")
async def sync_inbox(payload: SyncRequest, request: Request, tenant_id: str = Depends(require_tenant)):
    """
    Fetches messages received in the last 24h, runs each through the triage/draft agent, and persists
    anything that needs a human decision. Streams progress over SSE.

    This stands in for "checks email daily" — a scheduler calling this on an interval is a deployment
    concern, not built here (see README's "Known Design Trade-offs").
    """
    app_state = request.app.state
    limiter_result = await app_state.rate_limiter.acquire(tenant_id)
    if not limiter_result.allowed:
        raise HTTPException(status_code=429, detail="tenant rate limit exceeded, please retry shortly")

    email_provider, calendar_provider = await build_providers_for_tenant(app_state, tenant_id)

    workflow = AssistantWorkflow(app_state.llm_client, calendar_provider)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    messages = await email_provider.list_recent_messages(since)

    async def event_generator():
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
                        # Only persist on state-transition checkpoints, not on every streamed token —
                        # otherwise a long draft would trigger one DB write per chunk.
                        await repo.update_state(
                            ctx_snapshot.action_id,
                            state=ctx_snapshot.state,
                            draft_content=ctx_snapshot.draft_content,
                        )

                    yield {
                        "event": "delta",
                        "data": json.dumps(
                            {
                                "action_id": ctx_snapshot.action_id,
                                "message_id": message.message_id,
                                "state": ctx_snapshot.state,
                                "delta": chunk,
                            }
                        ),
                    }

                if persisted:
                    actions_created += 1

        if actions_created and payload.auto_notify_manager:
            await app_state.notifier.send(
                NotificationRequest(
                    tenant_id=tenant_id,
                    channel=NotifyChannel.EMAIL,
                    recipient="owner@example.com",
                    subject=f"{actions_created} item(s) awaiting your review",
                    body="Log in to review AI-drafted replies awaiting your approval.",
                    idempotency_key=f"{tenant_id}:{datetime.now(timezone.utc).date()}:digest",
                )
            )

        yield {"event": "done", "data": json.dumps({"actions_created": actions_created})}

    return EventSourceResponse(event_generator())


@router.get("/assistant", response_model=list[PendingActionResponse])
async def list_actions(request: Request, tenant_id: str = Depends(require_tenant)):
    """All of this tenant's actions (pending and already-decided), newest first — the frontend splits
    this into the pending queue and the "recently handled" history client-side."""
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        return await repo.list_all(tenant_id)


@router.get("/assistant/pending", response_model=list[PendingActionResponse])
async def list_pending_actions(request: Request, tenant_id: str = Depends(require_tenant)):
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        return await repo.list_pending(tenant_id)


@router.get("/assistant/{action_id}", response_model=PendingActionResponse)
async def get_action(action_id: str, request: Request, tenant_id: str = Depends(require_tenant)):
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        try:
            record = await repo.get(action_id)
        except ActionNotFoundError:
            raise HTTPException(status_code=404, detail="action not found") from None
    if record.tenant_id != tenant_id:
        # Same 404 as "doesn't exist" — never confirm to a caller that another tenant's action exists.
        raise HTTPException(status_code=404, detail="action not found")
    return record


@router.post("/assistant/{action_id}/decision", response_model=PendingActionResponse)
async def submit_action_decision(
    action_id: str, payload: ActionDecisionRequest, request: Request, tenant_id: str = Depends(require_tenant)
):
    """
    Records a merchant's approve/edit/reject decision. Approving or editing actually sends the reply
    via the configured EmailProvider — this is the human-review gate: AI drafts, a person decides what
    actually goes out under their name.
    """
    app_state = request.app.state
    email_provider, _calendar_provider = await build_providers_for_tenant(app_state, tenant_id)
    async with repository_session(app_state.db_sessionmaker, email_provider) as repo:
        try:
            record = await repo.get(action_id)
        except ActionNotFoundError:
            raise HTTPException(status_code=404, detail="action not found") from None
        if record.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="action not found")

        try:
            return await repo.record_decision(
                action_id=action_id,
                decision=payload.decision,
                decided_by=payload.decided_by,
                edited_reply=payload.edited_reply,
                reject_reason=payload.reject_reason,
            )
        except InvalidDecisionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
