from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agents.review_agent import WorkflowContext
from app.api.routes import router
from app.db.repository import repository_session
from app.models.schemas import ReviewInput, WorkflowState


@pytest_asyncio.fixture
async def client(db_sessionmaker):
    # These endpoints only touch app.state.db_sessionmaker, so we don't need the full lifespan
    # (Redis, LLM client, etc.) that app/main.py wires up for /reviews/process.
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.db_sessionmaker = db_sessionmaker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_awaiting_approval(db_sessionmaker, *, tenant_id: str = "tenant-1", reply_draft: str = "Thanks!") -> str:
    ctx = WorkflowContext(
        tenant_id=tenant_id,
        review=ReviewInput(review_id="rev-1", author="Alice", rating=4, text="Good food, slow service."),
    )
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = reply_draft
        await repo.sync_snapshot(ctx)
    return ctx.workflow_id


async def test_get_workflow_returns_404_when_missing(client):
    response = await client.get("/api/v1/reviews/does-not-exist")
    assert response.status_code == 404


async def test_get_workflow_returns_record(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.get(f"/api/v1/reviews/{workflow_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == workflow_id
    assert body["state"] == "awaiting_approval"
    assert body["reply_draft"] == "Thanks!"


async def test_list_pending_filters_by_tenant(client, db_sessionmaker):
    tenant_1_id = await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-1")
    await _seed_awaiting_approval(db_sessionmaker, tenant_id="tenant-2")

    response = await client.get("/api/v1/reviews/pending", params={"tenant_id": "tenant-1"})

    assert response.status_code == 200
    body = response.json()
    assert [record["id"] for record in body] == [tenant_1_id]


async def test_approve_decision_sets_final_reply_to_draft(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker, reply_draft="We appreciate your feedback.")

    response = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "approved"
    assert body["decision"] == "approved"
    assert body["final_reply"] == "We appreciate your feedback."


async def test_edited_decision_requires_edited_reply(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={"decision": "edited", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 422


async def test_edited_decision_uses_edited_reply(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={
            "decision": "edited",
            "decided_by": "owner@example.com",
            "edited_reply": "A human-polished reply.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["final_reply"] == "A human-polished reply."


async def test_rejected_decision_clears_final_reply(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker)

    response = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={"decision": "rejected", "decided_by": "owner@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "rejected"
    assert body["final_reply"] is None


async def test_decision_on_unknown_workflow_returns_404(client):
    response = await client.post(
        "/api/v1/reviews/does-not-exist/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )
    assert response.status_code == 404


async def test_decision_on_already_decided_workflow_returns_409(client, db_sessionmaker):
    workflow_id = await _seed_awaiting_approval(db_sessionmaker)

    first = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={"decision": "approved", "decided_by": "owner@example.com"},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/reviews/{workflow_id}/decision",
        json={"decision": "rejected", "decided_by": "owner@example.com"},
    )
    assert second.status_code == 409
