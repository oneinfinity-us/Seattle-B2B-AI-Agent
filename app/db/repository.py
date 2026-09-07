from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import PendingActionRecord
from app.integrations.email_provider import EmailProvider
from app.models.schemas import ActionKind, ActionState, DecisionType


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
        self, action_id: str, *, state: ActionState, draft_content: str | None = None
    ) -> None:
        record = await self._get(action_id)
        record.state = state
        if draft_content is not None:
            record.draft_content = draft_content
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
