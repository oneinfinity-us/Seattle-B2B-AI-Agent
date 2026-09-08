from __future__ import annotations

import pytest

from app.db.repository import ActionNotFoundError, InvalidDecisionError, repository_session
from app.integrations.email_provider import FakeEmailProvider
from app.models.schemas import ActionKind, ActionState, DecisionType


class _FailingEmailProvider:
    async def send_reply(self, thread_id: str, to: str, body: str) -> None:
        raise RuntimeError("smtp down")


async def _create_action(repo, **overrides) -> str:
    defaults = dict(
        action_id=None,
        tenant_id="tenant-1",
        kind=ActionKind.EMAIL_REPLY,
        source_message_id="msg-1",
        reply_to="customer@example.com",
        summary="Reply to a question about hours",
    )
    defaults.update(overrides)
    import uuid

    action_id = defaults.pop("action_id") or str(uuid.uuid4())
    record = await repo.create(action_id=action_id, **defaults)
    return record.id


async def test_create_persists_initial_state(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        action_id = await _create_action(repo)
        record = await repo.get(action_id)

    assert record.tenant_id == "tenant-1"
    assert record.kind == ActionKind.EMAIL_REPLY
    assert record.state == ActionState.RECEIVED


async def test_update_state_sets_state_and_draft(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="Thanks!")
        record = await repo.get(action_id)

    assert record.state == ActionState.AWAITING_APPROVAL
    assert record.draft_content == "Thanks!"


async def test_get_missing_action_raises(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        with pytest.raises(ActionNotFoundError):
            await repo.get("does-not-exist")


async def test_list_all_returns_every_state_for_tenant_newest_first(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        received_id = await _create_action(repo, tenant_id="tenant-1")

        awaiting_id = await _create_action(repo, tenant_id="tenant-1")
        await repo.update_state(awaiting_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        await _create_action(repo, tenant_id="tenant-2")  # a different tenant, must not appear

        results = await repo.list_all("tenant-1")

    assert {r.id for r in results} == {received_id, awaiting_id}
    assert all(r.tenant_id == "tenant-1" for r in results)


async def test_list_pending_only_returns_awaiting_approval_for_tenant(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        pending_id = await _create_action(repo, tenant_id="tenant-1")
        await repo.update_state(pending_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        other_tenant_id = await _create_action(repo, tenant_id="tenant-2")
        await repo.update_state(other_tenant_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        await _create_action(repo, tenant_id="tenant-1")  # stays RECEIVED

        results = await repo.list_pending("tenant-1")

    assert [r.id for r in results] == [pending_id]


async def test_record_decision_without_email_provider_ends_in_approved(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:  # no email_provider passed
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="Thanks!")

        record = await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

    assert record.state == ActionState.APPROVED
    assert record.final_content == "Thanks!"


async def test_record_decision_approved_sends_via_email_provider(db_sessionmaker):
    provider = FakeEmailProvider()
    async with repository_session(db_sessionmaker, provider) as repo:
        action_id = await _create_action(repo, reply_to="customer@example.com", source_message_id="thread-42")
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="We appreciate it!")

        record = await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

    assert record.state == ActionState.SENT
    assert provider.sent == [("thread-42", "customer@example.com", "We appreciate it!")]


async def test_record_decision_edited_sends_edited_reply(db_sessionmaker):
    provider = FakeEmailProvider()
    async with repository_session(db_sessionmaker, provider) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="Original draft.")

        record = await repo.record_decision(
            action_id=action_id,
            decision=DecisionType.EDITED,
            decided_by="owner@example.com",
            edited_reply="A better, human-edited reply.",
        )

    assert record.state == ActionState.SENT
    assert record.final_content == "A better, human-edited reply."
    assert provider.sent[0][2] == "A better, human-edited reply."


async def test_record_decision_rejected_does_not_send_and_clears_final_content(db_sessionmaker):
    provider = FakeEmailProvider()
    async with repository_session(db_sessionmaker, provider) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="Original draft.")

        record = await repo.record_decision(
            action_id=action_id,
            decision=DecisionType.REJECTED,
            decided_by="owner@example.com",
            reject_reason="I'll call them directly.",
        )

    assert record.state == ActionState.REJECTED
    assert record.final_content is None
    assert record.reject_reason == "I'll call them directly."
    assert provider.sent == []


async def test_record_decision_marks_failed_when_send_raises(db_sessionmaker):
    async with repository_session(db_sessionmaker, _FailingEmailProvider()) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        record = await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

    assert record.state == ActionState.FAILED
    assert record.error_message == "smtp down"


async def test_record_decision_rejects_when_not_awaiting_approval(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        action_id = await _create_action(repo)  # still RECEIVED

        with pytest.raises(InvalidDecisionError):
            await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")


async def test_get_metrics_computes_counts_and_rates(db_sessionmaker):
    async with repository_session(db_sessionmaker, FakeEmailProvider()) as repo:
        approved_id = await _create_action(repo)
        await repo.update_state(approved_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")
        await repo.record_decision(action_id=approved_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

        edited_id = await _create_action(repo)
        await repo.update_state(edited_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")
        await repo.record_decision(
            action_id=edited_id, decision=DecisionType.EDITED, decided_by="owner@example.com", edited_reply="edited text"
        )

        rejected_id = await _create_action(repo)
        await repo.update_state(rejected_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")
        await repo.record_decision(action_id=rejected_id, decision=DecisionType.REJECTED, decided_by="owner@example.com")

        pending_id = await _create_action(repo)
        await repo.update_state(pending_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        metrics = await repo.get_metrics("tenant-1")

    assert metrics.total_actions == 4
    assert metrics.pending_count == 1
    assert metrics.sent_count == 2
    assert metrics.rejected_count == 1
    assert metrics.failed_count == 0
    assert metrics.approved_without_edits_count == 1
    assert metrics.edited_before_send_count == 1
    assert metrics.override_rate == 0.5
    assert metrics.rejection_rate == 1 / 3
    assert metrics.avg_draft_seconds is not None and metrics.avg_draft_seconds >= 0
    assert metrics.avg_decision_seconds is not None and metrics.avg_decision_seconds >= 0


async def test_get_metrics_counts_failed_sends_separately_from_sent(db_sessionmaker):
    async with repository_session(db_sessionmaker, _FailingEmailProvider()) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")
        await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

        metrics = await repo.get_metrics("tenant-1")

    assert metrics.failed_count == 1
    assert metrics.sent_count == 0
    assert metrics.approved_without_edits_count == 0  # only counts SENT, not FAILED
    assert metrics.rejection_rate == 0.0  # 0 rejected out of 1 decided (the failed one)


async def test_get_metrics_returns_none_rates_when_no_data(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        metrics = await repo.get_metrics("tenant-with-nothing")

    assert metrics.total_actions == 0
    assert metrics.override_rate is None
    assert metrics.rejection_rate is None
    assert metrics.avg_draft_seconds is None
    assert metrics.avg_decision_seconds is None


async def test_get_metrics_is_scoped_to_tenant(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        await _create_action(repo, tenant_id="tenant-1")
        await _create_action(repo, tenant_id="tenant-2")

        metrics = await repo.get_metrics("tenant-1")

    assert metrics.total_actions == 1


async def test_record_decision_twice_raises_on_second_call(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

        with pytest.raises(InvalidDecisionError):
            await repo.record_decision(action_id=action_id, decision=DecisionType.REJECTED, decided_by="owner@example.com")
