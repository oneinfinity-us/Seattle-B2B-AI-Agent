from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.review_agent import WorkflowContext
from app.db.models import ReviewWorkflowRecord
from app.models.schemas import DecisionType, WorkflowState


class WorkflowNotFoundError(Exception):
    """Raised when a workflow_id has no matching persisted record."""


class InvalidDecisionError(Exception):
    """Raised when a decision is submitted for a workflow that isn't awaiting approval."""


class WorkflowRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, ctx: WorkflowContext) -> ReviewWorkflowRecord:
        record = ReviewWorkflowRecord(
            id=ctx.workflow_id,
            tenant_id=ctx.tenant_id,
            review_id=ctx.review.review_id,
            review_text=ctx.review.text,
            state=ctx.state,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def sync_snapshot(self, ctx: WorkflowContext) -> None:
        record = await self._get(ctx.workflow_id)
        record.sentiment = ctx.sentiment
        record.state = ctx.state
        record.reply_draft = ctx.reply_draft
        await self._session.commit()

    async def get(self, workflow_id: str) -> ReviewWorkflowRecord:
        return await self._get(workflow_id)

    async def list_pending(self, tenant_id: str) -> list[ReviewWorkflowRecord]:
        stmt = select(ReviewWorkflowRecord).where(
            ReviewWorkflowRecord.tenant_id == tenant_id,
            ReviewWorkflowRecord.state == WorkflowState.AWAITING_APPROVAL,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def record_decision(
        self,
        workflow_id: str,
        decision: DecisionType,
        decided_by: str,
        edited_reply: str | None = None,
    ) -> ReviewWorkflowRecord:
        record = await self._get(workflow_id)
        if record.state != WorkflowState.AWAITING_APPROVAL:
            raise InvalidDecisionError(
                f"workflow {workflow_id} is in state '{record.state}', not awaiting approval"
            )

        if decision == DecisionType.REJECTED:
            record.state = WorkflowState.REJECTED
            record.final_reply = None
        else:
            record.state = WorkflowState.APPROVED
            record.final_reply = edited_reply if decision == DecisionType.EDITED else record.reply_draft

        record.decision = decision
        record.decided_by = decided_by
        record.decided_at = datetime.now(timezone.utc)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def _get(self, workflow_id: str) -> ReviewWorkflowRecord:
        record = await self._session.get(ReviewWorkflowRecord, workflow_id)
        if record is None:
            raise WorkflowNotFoundError(workflow_id)
        return record


@asynccontextmanager
async def repository_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[WorkflowRepository]:
    async with sessionmaker() as session:
        yield WorkflowRepository(session)
