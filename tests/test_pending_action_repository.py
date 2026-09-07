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

        record = await repo.record_decision(action_id=action_id, decision=DecisionType.REJECTED, decided_by="owner@example.com")

    assert record.state == ActionState.REJECTED
    assert record.final_content is None
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


async def test_record_decision_twice_raises_on_second_call(db_sessionmaker):
    async with repository_session(db_sessionmaker) as repo:
        action_id = await _create_action(repo)
        await repo.update_state(action_id, state=ActionState.AWAITING_APPROVAL, draft_content="draft")

        await repo.record_decision(action_id=action_id, decision=DecisionType.APPROVED, decided_by="owner@example.com")

        with pytest.raises(InvalidDecisionError):
            await repo.record_decision(action_id=action_id, decision=DecisionType.REJECTED, decided_by="owner@example.com")
