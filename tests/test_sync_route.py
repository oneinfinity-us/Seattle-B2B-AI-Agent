from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import router
from app.core.llm_client import TokenUsage
from app.core.rate_limiter import RateLimitResult
from app.core.sessions import require_tenant
from app.integrations.calendar_provider import FakeCalendarProvider
from app.integrations.email_provider import FakeEmailProvider
from app.models.schemas import EmailMessage


class _StubLLMClient:
    async def stream_reply_draft(self, system: str, prompt: str, usage: TokenUsage | None = None) -> AsyncGenerator[str, None]:
        yield "Thanks for reaching out!"


class _AllowRateLimiter:
    # A fake, not TenantRateLimiter against fakeredis -- fakeredis doesn't implement EVAL/EVALSHA
    # without an optional extra, and the real limiter's token bucket is Lua-scripted.
    async def acquire(self, tenant_id: str, cost: float = 1.0) -> RateLimitResult:
        return RateLimitResult(allowed=True, remaining_tokens=100.0)


class _SpyNotifier:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, request) -> bool:
        self.sent.append(request)
        return True


@pytest_asyncio.fixture
async def notifier():
    return _SpyNotifier()


@pytest_asyncio.fixture
async def client(db_sessionmaker, notifier):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.db_sessionmaker = db_sessionmaker
    app.state.email_provider = FakeEmailProvider(
        [
            EmailMessage(
                message_id="msg-1", thread_id="thread-1", sender="customer@example.com",
                subject="Question about your hours", body="Are you open Sundays?", received_at=datetime.now(timezone.utc),
            )
        ]
    )
    app.state.calendar_provider = FakeCalendarProvider()
    app.state.llm_client = _StubLLMClient()
    app.state.notifier = notifier
    app.state.rate_limiter = _AllowRateLimiter()
    app.dependency_overrides[require_tenant] = lambda: "owner@example.com"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_sync_route_notifies_the_logged_in_tenant_not_a_placeholder(client, notifier):
    response = await client.post("/api/v1/assistant/sync", json={"auto_notify_manager": True})

    assert response.status_code == 200
    assert len(notifier.sent) == 1
    assert notifier.sent[0].recipient == "owner@example.com"
