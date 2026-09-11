from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.llm_client import TokenUsage
from app.core.rate_limiter import RateLimitResult
from app.db.credential_repository import CredentialRepository
from app.db.repository import repository_session
from app.integrations.calendar_provider import FakeCalendarProvider
from app.integrations.email_provider import FakeEmailProvider
from app.models.schemas import ActionState, EmailMessage
from app.services import sync_service


class _StubLLMClient:
    async def stream_reply_draft(self, system: str, prompt: str, usage: TokenUsage | None = None) -> AsyncGenerator[str, None]:
        if usage is not None:
            usage.input_tokens = 50
            usage.output_tokens = 20
            usage.estimated_cost_usd = 0.0005
        yield "Thanks, "
        yield "we'll follow up soon."


class _AllowRateLimiter:
    # A fake, not TenantRateLimiter against fakeredis -- fakeredis doesn't implement EVAL/EVALSHA
    # without an optional extra, and the real limiter's token bucket is Lua-scripted.
    async def acquire(self, tenant_id: str, cost: float = 1.0) -> RateLimitResult:
        return RateLimitResult(allowed=True, remaining_tokens=100.0)


class _DenyRateLimiter:
    async def acquire(self, tenant_id: str, cost: float = 1.0) -> RateLimitResult:
        return RateLimitResult(allowed=False, remaining_tokens=0.0)


class _SpyNotifier:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, request) -> bool:
        self.sent.append(request)
        return True


def _make_message(**overrides) -> EmailMessage:
    defaults = dict(
        message_id="msg-1",
        thread_id="thread-1",
        sender="customer@example.com",
        subject="Question about your hours",
        body="Are you open on Sundays?",
        received_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return EmailMessage(**defaults)


def _make_app_state(db_sessionmaker, messages=None, rate_limiter=None):
    return SimpleNamespace(
        db_sessionmaker=db_sessionmaker,
        rate_limiter=rate_limiter or _AllowRateLimiter(),
        llm_client=_StubLLMClient(),
        notifier=_SpyNotifier(),
        email_provider=FakeEmailProvider(messages or []),
        calendar_provider=FakeCalendarProvider(),
    )


async def test_sync_tenant_inbox_creates_action_and_persists_token_usage(db_sessionmaker):
    message = _make_message()
    app_state = _make_app_state(db_sessionmaker, messages=[message])

    created = await sync_service.sync_tenant_inbox(app_state, "owner@example.com")

    assert created == 1
    async with repository_session(db_sessionmaker) as repo:
        records = await repo.list_all("owner@example.com")
    assert len(records) == 1
    assert records[0].state == ActionState.AWAITING_APPROVAL
    assert records[0].draft_content == "Thanks, we'll follow up soon."
    assert records[0].input_tokens == 50
    assert records[0].output_tokens == 20
    assert records[0].estimated_cost_usd == 0.0005


async def test_sync_tenant_inbox_notifies_the_tenant_itself_not_a_placeholder(db_sessionmaker):
    app_state = _make_app_state(db_sessionmaker, messages=[_make_message()])

    await sync_service.sync_tenant_inbox(app_state, "owner@example.com")

    assert len(app_state.notifier.sent) == 1
    assert app_state.notifier.sent[0].recipient == "owner@example.com"


async def test_sync_tenant_inbox_skips_notification_when_disabled(db_sessionmaker):
    app_state = _make_app_state(db_sessionmaker, messages=[_make_message()])

    await sync_service.sync_tenant_inbox(app_state, "owner@example.com", auto_notify_manager=False)

    assert app_state.notifier.sent == []


async def test_sync_tenant_inbox_returns_zero_and_does_nothing_when_rate_limited(db_sessionmaker):
    app_state = _make_app_state(db_sessionmaker, messages=[_make_message()], rate_limiter=_DenyRateLimiter())

    created = await sync_service.sync_tenant_inbox(app_state, "owner@example.com")

    assert created == 0
    assert app_state.notifier.sent == []
    async with repository_session(db_sessionmaker) as repo:
        assert await repo.list_all("owner@example.com") == []


async def test_sync_all_tenants_continues_after_one_tenant_fails(db_sessionmaker, monkeypatch):
    async with db_sessionmaker() as session:
        repo = CredentialRepository(session)
        await repo.upsert(
            tenant_id="tenant-a@example.com", business_name="", refresh_token="r", access_token="a",
            token_expiry=None, scopes=["openid"],
        )
        await repo.upsert(
            tenant_id="tenant-b@example.com", business_name="", refresh_token="r", access_token="a",
            token_expiry=None, scopes=["openid"],
        )

    calls: list[str] = []

    async def fake_sync_tenant_inbox(app_state, tenant_id, **kwargs):
        calls.append(tenant_id)
        if tenant_id == "tenant-a@example.com":
            raise RuntimeError("simulated failure, e.g. a revoked Google grant")
        return 3

    monkeypatch.setattr(sync_service, "sync_tenant_inbox", fake_sync_tenant_inbox)

    await sync_service.sync_all_tenants(SimpleNamespace(db_sessionmaker=db_sessionmaker))

    assert set(calls) == {"tenant-a@example.com", "tenant-b@example.com"}
