from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PendingActionRecord
from app.integrations.email_provider import EmailProvider
from app.models.schemas import ActionKind, ActionState, DecisionType, MetricsSummary


def _seconds_between(later: datetime, earlier: datetime) -> float:
    # SQLite doesn't reliably round-trip tzinfo the way Postgres does; normalize both sides to naive
    # (everything is already stored as UTC) so the subtraction never raises on an aware/naive mismatch.
    return (later.replace(tzinfo=None) - earlier.replace(tzinfo=None)).total_seconds()


class ActionNotFoundError(Exception):
    """Raised when an action_id has no matching persisted record."""


class InvalidDecisionError(Exception):
    """Raised when a decision is submitted for an action that isn't awaiting approval."""


class PendingActionRepository:
    def __init__(self, session: AsyncSession, email_provider: EmailProvider | None = None):
        self._session = session
        self._email_provider = email_provider

    async def create(
        self,
        *,
        action_id: str,
        tenant_id: str,
        kind: ActionKind,
        source_message_id: str,
        reply_to: str,
        summary: str,
    ) -> PendingActionRecord:
        record = PendingActionRecord(
            id=action_id,
            tenant_id=tenant_id,
            kind=kind,
            source_message_id=source_message_id,
            reply_to=reply_to,
            summary=summary,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def update_state(
        self,
        action_id: str,
        *,
        state: ActionState,
        draft_content: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        record = await self._get(action_id)
        record.state = state
        if draft_content is not None:
            record.draft_content = draft_content
        if input_tokens is not None:
            record.input_tokens = input_tokens
        if output_tokens is not None:
            record.output_tokens = output_tokens
        if estimated_cost_usd is not None:
            record.estimated_cost_usd = estimated_cost_usd
        if state == ActionState.AWAITING_APPROVAL and record.drafted_at is None:
            record.drafted_at = datetime.now(timezone.utc)
        await self._session.commit()

    async def get(self, action_id: str) -> PendingActionRecord:
        return await self._get(action_id)

    async def list_pending(self, tenant_id: str) -> list[PendingActionRecord]:
        stmt = select(PendingActionRecord).where(
            PendingActionRecord.tenant_id == tenant_id,
            PendingActionRecord.state == ActionState.AWAITING_APPROVAL,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, tenant_id: str, limit: int = 50) -> list[PendingActionRecord]:
        stmt = (
            select(PendingActionRecord)
            .where(PendingActionRecord.tenant_id == tenant_id)
            .order_by(PendingActionRecord.updated_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def record_decision(
        self,
        action_id: str,
        decision: DecisionType,
        decided_by: str,
        edited_reply: str | None = None,
        reject_reason: str | None = None,
    ) -> PendingActionRecord:
        record = await self._get(action_id)
        if record.state != ActionState.AWAITING_APPROVAL:
            raise InvalidDecisionError(f"action {action_id} is in state '{record.state}', not awaiting approval")

        record.decision = decision
        record.decided_by = decided_by
        record.decided_at = datetime.now(timezone.utc)

        if decision == DecisionType.REJECTED:
            record.state = ActionState.REJECTED
            record.final_content = None
            record.reject_reason = reject_reason
            await self._session.commit()
            await self._session.refresh(record)
            return record

        record.final_content = edited_reply if decision == DecisionType.EDITED else record.draft_content

        if self._email_provider is not None:
            try:
                await self._email_provider.send_reply(record.source_message_id, record.reply_to, record.final_content)
                record.state = ActionState.SENT
            except Exception as exc:  # noqa: BLE001 - a send failure should land the action in FAILED, not crash the request
                record.state = ActionState.FAILED
                record.error_message = str(exc)
        else:
            record.state = ActionState.APPROVED

        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def get_metrics(self, tenant_id: str) -> MetricsSummary:
        stmt = select(PendingActionRecord).where(PendingActionRecord.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        records = list(result.scalars().all())

        pending_count = sum(1 for r in records if r.state == ActionState.AWAITING_APPROVAL)
        sent_count = sum(1 for r in records if r.state == ActionState.SENT)
        rejected_count = sum(1 for r in records if r.state == ActionState.REJECTED)
        failed_count = sum(1 for r in records if r.state == ActionState.FAILED)

        approved_without_edits_count = sum(
            1 for r in records if r.state == ActionState.SENT and r.decision == DecisionType.APPROVED
        )
        edited_before_send_count = sum(
            1 for r in records if r.state == ActionState.SENT and r.decision == DecisionType.EDITED
        )

        decided_sends = approved_without_edits_count + edited_before_send_count
        override_rate = edited_before_send_count / decided_sends if decided_sends else None

        all_decided = sent_count + rejected_count + failed_count
        rejection_rate = rejected_count / all_decided if all_decided else None

        draft_durations = [
            _seconds_between(r.drafted_at, r.created_at) for r in records if r.drafted_at is not None
        ]
        avg_draft_seconds = sum(draft_durations) / len(draft_durations) if draft_durations else None

        decision_durations = [
            _seconds_between(r.decided_at, r.drafted_at)
            for r in records
            if r.decided_at is not None and r.drafted_at is not None
        ]
        avg_decision_seconds = (
            sum(decision_durations) / len(decision_durations) if decision_durations else None
        )

        total_input_tokens = sum(r.input_tokens or 0 for r in records)
        total_output_tokens = sum(r.output_tokens or 0 for r in records)
        total_estimated_cost_usd = sum(r.estimated_cost_usd or 0.0 for r in records)
        costed_actions = [r for r in records if r.estimated_cost_usd is not None]
        avg_cost_per_action = (
            sum(r.estimated_cost_usd for r in costed_actions) / len(costed_actions) if costed_actions else None
        )

        return MetricsSummary(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_estimated_cost_usd=total_estimated_cost_usd,
            avg_cost_per_action=avg_cost_per_action,
            total_actions=len(records),
            pending_count=pending_count,
            sent_count=sent_count,
            rejected_count=rejected_count,
            failed_count=failed_count,
            approved_without_edits_count=approved_without_edits_count,
            edited_before_send_count=edited_before_send_count,
            override_rate=override_rate,
            rejection_rate=rejection_rate,
            avg_draft_seconds=avg_draft_seconds,
            avg_decision_seconds=avg_decision_seconds,
        )

    async def _get(self, action_id: str) -> PendingActionRecord:
        record = await self._session.get(PendingActionRecord, action_id)
        if record is None:
            raise ActionNotFoundError(action_id)
        return record


@asynccontextmanager
async def repository_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    email_provider: EmailProvider | None = None,
) -> AsyncIterator[PendingActionRepository]:
    async with sessionmaker() as session:
        yield PendingActionRepository(session, email_provider)
