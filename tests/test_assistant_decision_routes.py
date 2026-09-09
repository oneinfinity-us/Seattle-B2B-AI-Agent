from __future__ import annotations

import uuid

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import router
from app.core.sessions import require_tenant
from app.db.repository import repository_session
from app.integrations.calendar_provider import FakeCalendarProvider
from app.integrations.email_provider import FakeEmailProvider
from app.models.schemas import ActionKind, ActionState, DecisionType


@pytest_asyncio.fixture
async def email_provider():
    return FakeEmailProvider()


@pytest_asyncio.fixture
async def client(db_sessionmaker, email_provider):
    # These endpoints only touch app.state.db_sessionmaker/email_provider (no tenant here has a stored
    # GmailAccountCredential, so build_providers_for_tenant always falls back to these fakes), so we
    # don't need the full lifespan (Redis, LLM client, etc.) that app/main.py wires up.
    # require_tenant is overridden instead of going through a real session cookie + Redis.
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.db_sessionmaker = db_sessionmaker
    app.state.email_provider = email_provider
    app.state.calendar_provider = FakeCalendarProvider()
    app.dependency_overrides[require_tenant] = lambda: "tenant-1"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_awaiting_approval(
    db_sessionmaker, *, tenant_id: str = "tenant-1", reply_to: str = "customer@example.com", draft_content: str = "Thanks!"
) -> str:
    action_id = str(uuid.uuid4())
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(
            action_id=action_id,
            tenant_id=tenant_id,
            kind=ActionKind.EMAIL_REPLY,
            source_message_id="thread-1",
            reply_to=reply_to,
            summary="Reply to a customer question",
        )
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content=draft_content)
    return action_id


async def test_get_metrics_returns_summary_for_the_logged_in_tenant(client, db_sessionmaker):
    await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-1")
    await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    response = await client.get("/api/v1/assistant/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["total_actions"] == 1
    assert body["pending_count"] == 1
    assert body["total_input_tokens"] == 0
    assert body["total_output_tokens"] == 0
    assert body["total_estimated_cost_usd"] == 0.0
    assert body["avg_cost_per_action"] is None


async def test_get_action_returns_404_when_missing(client):
    response = await client.get("/api/v1/assistant/does-not-exist")
    assert response.status_code == 404


async def test_get_action_returns_record(client, db_sessionmaker):
    action_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.get(f"/api/v1/assistant/{action_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == action_id
    assert body["state"] == "awaiting_approval"
    assert body["draft_content"] == "Thanks!"


async def test_list_pending_filters_by_tenant(client, db_sessionmaker):
    tenant_1_id = await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-1")
    await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    # The client fixture's session resolves to "tenant-1" (see require_tenant override) — no tenant_id
    # is or can be supplied by the caller.
    response = await client.get("/api/v1/assistant/pending")

    assert response.status_code == 200
    assert [record["id"] for record in response.json()] == [tenant_1_id]


async def test_list_all_actions_returns_pending_and_history_for_tenant(client, db_sessionmaker):
    pending_id = await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-1")
    async with repository_session(db_sessionmaker) as repo:
        sent_id = (
            await repo.create(
                action_id="sent-1",
                tenant_id="tenant-1",
                kind=ActionKind.EMAIL_REPLY,
                source_message_id="thread-2",
                reply_to="other@example.com",
                summary="Already handled",
            )
        ).id
        await repo.update_state(sent_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")
        await repo.record_decision(action_id=sent_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")
    await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    response = await client.get("/api/v1/assistant")

    assert response.status_code == 200
    ids = {record["id"] for record in response.json()}
    assert ids == {pending_id, sent_id}


async def test_get_action_owned_by_another_tenant_returns_404(client, db_sessionmaker):
    other_tenant_action_id = await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    response = await client.get(f"/api/v1/assistant/{other_tenant_action_id}")

    assert response.status_code == 404


async def test_decision_on_another_tenants_action_returns_404(client, db_sessionmaker):
    other_tenant_action_id = await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    response = await client.post(
        f"/api/v1/assistant/{other_tenant_action_id}/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 404


async def test_approve_decision_sends_reply_and_marks_sent(client, db_sessionmaker, email_provider):
    action_id = await _seed_awaiting_approval(db_sessionmaker, reply_to="customer@example.com", draft_content="We appreciate your note.")

    response = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "sent"
    assert body["final_content"] == "We appreciate your note."
    assert email_provider.sent == [("thread-1", "customer@example.com", "We appreciate your note.")]


async def test_edited_decision_requires_edited_reply(client, db_sessionmaker):
    action_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "edited", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 422


async def test_edited_decision_sends_edited_reply(client, db_sessionmaker, email_provider):
    action_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "edited", "decided_by": "owner@example.com", "edited_reply": "A human-polished reply."},
    )

    assert response.status_code == 200
    assert response.json()["final_content"] == "A human-polished reply."
    assert email_provider.sent[0][2] == "A human-polished reply."


async def test_rejected_decision_does_not_send(client, db_sessionmaker, email_provider):
    action_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "rejected", "decided_by": "owner@example.com", "reject_reason": "I'll call them directly."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "rejected"
    assert body["final_content"] is None
    assert body["reject_reason"] == "I'll call them directly."
    assert email_provider.sent == []


async def test_decision_on_unknown_action_returns_404(client):
    response = await client.post(
        "/api/v1/assistant/does-not-exist/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )
    assert response.status_code == 404


async def test_decision_on_already_decided_action_returns_409(client, db_sessionmaker):
    action_id = await _seed_awaiting_approval(db_sessionmaker)

    first = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/assistant/{action_id}/decision",
        json={"decision": "rejected", "decided_by": "owner@example.com"},
    )
    assert second.status_code == 409
