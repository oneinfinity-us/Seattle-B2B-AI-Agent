from __future__ import annotations

import pytest

from app.agents.review_agent import WorkflowContext
from app.db.repository import (
    InvalidDecisionError,
    WorkflowNotFoundError,
    repository_session,
)
from app.models.schemas import DecisionType, ReviewInput, Sentiment, WorkflowState


def _make_context(**overrides) -> WorkflowContext:
    defaults = dict(
        tenant_id="tenant-1",
        review=ReviewInput(review_id="rev-1", author="Alice", rating=2, text="Service was slow."),
    )
    defaults.update(overrides)
    return WorkflowContext(**defaults)


async def test_create_persists_initial_state(db_sessionmaker):
    ctx = _make_context()

    async with repository_session(db_sessionmaker) as repo:
        record = await repo.create(ctx)

    assert record.id == ctx.workflow_id
    assert record.tenant_id == "tenant-1"
    assert record.review_id == "rev-1"
    assert record.state == WorkflowState.RECEIVED


async def test_sync_snapshot_updates_state_sentiment_and_draft(db_sessionmaker):
    ctx = _make_context()

    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)

        ctx.sentiment = Sentiment.NEGATIVE
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = "We're sorry to hear that."
        await repo.sync_snapshot(ctx)

        record = await repo.get(ctx.workflow_id)

    assert record.sentiment == Sentiment.NEGATIVE
    assert record.state == WorkflowState.AWAITING_APPROVAL
    assert record.reply_draft == "We're sorry to hear that."


async def test_get_missing_workflow_raises(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        with pytest.raises(WorkflowNotFoundError):
            await repo.get("does-not-exist")


async def test_list_pending_only_returns_awaiting_approval_for_tenant(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        pending = _make_context(tenant_id="tenant-1")
        await repo.create(pending)
        pending.state = WorkflowState.AWAITING_APPROVAL
        pending.reply_draft = "draft"
        await repo.sync_snapshot(pending)

        other_tenant = _make_context(tenant_id="tenant-2")
        await repo.create(other_tenant)
        other_tenant.state = WorkflowState.AWAITING_APPROVAL
        await repo.sync_snapshot(other_tenant)

        still_drafting = _make_context(tenant_id="tenant-1")
        await repo.create(still_drafting)  # stays in RECEIVED

        results = await repo.list_pending("tenant-1")

    assert [r.id for r in results] == [pending.workflow_id]


async def test_record_decision_approved_uses_draft_as_final_reply(db_sessionmaker):
    ctx = _make_context()
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = "Thanks for the feedback!"
        await repo.sync_snapshot(ctx)

        record = await repo.record_decision(
            workflow_id=ctx.workflow_id, decision=DecisionType.APPROVED, decided_by="owner@example.com"
        )

    assert record.state == WorkflowState.APPROVED
    assert record.decision == DecisionType.APPROVED
    assert record.decided_by == "owner@example.com"
    assert record.final_reply == "Thanks for the feedback!"
    assert record.decided_at is not None


async def test_record_decision_edited_uses_edited_reply(db_sessionmaker):
    ctx = _make_context()
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = "Original draft."
        await repo.sync_snapshot(ctx)

        record = await repo.record_decision(
            workflow_id=ctx.workflow_id,
            decision=DecisionType.EDITED,
            decided_by="owner@example.com",
            edited_reply="A better, human-edited reply.",
        )

    assert record.state == WorkflowState.APPROVED
    assert record.decision == DecisionType.EDITED
    assert record.final_reply == "A better, human-edited reply."


async def test_record_decision_rejected_clears_final_reply(db_sessionmaker):
    ctx = _make_context()
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = "Original draft."
        await repo.sync_snapshot(ctx)

        record = await repo.record_decision(
            workflow_id=ctx.workflow_id, decision=DecisionType.REJECTED, decided_by="owner@example.com"
        )

    assert record.state == WorkflowState.REJECTED
    assert record.final_reply is None


async def test_record_decision_rejects_when_not_awaiting_approval(db_sessionmaker):
    ctx = _make_context()
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)  # still in RECEIVED

        with pytest.raises(InvalidDecisionError):
            await repo.record_decision(
                workflow_id=ctx.workflow_id, decision=DecisionType.APPROVED, decided_by="owner@example.com"
            )


async def test_record_decision_twice_raises_on_second_call(db_sessionmaker):
    ctx = _make_context()
    async with repository_session(db_sessionmaker) as repo:
        await repo.create(ctx)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        ctx.reply_draft = "draft"
        await repo.sync_snapshot(ctx)

        await repo.record_decision(
            workflow_id=ctx.workflow_id, decision=DecisionType.APPROVED, decided_by="owner@example.com"
        )

        with pytest.raises(InvalidDecisionError):
            await repo.record_decision(
                workflow_id=ctx.workflow_id, decision=DecisionType.REJECTED, decided_by="owner@example.com"
            )
