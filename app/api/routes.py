from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agents.review_agent import ReviewWorkflow, WorkflowContext
from app.db.repository import InvalidDecisionError, WorkflowNotFoundError, repository_session
from app.models.schemas import (
    NotificationRequest,
    NotifyChannel,
    ProcessReviewRequest,
    WorkflowDecisionRequest,
    WorkflowRecordResponse,
)

router = APIRouter()


def _fake_embedding(text: str) -> list[float]:
    # Skeleton placeholder: in production, swap in a real embedding model call (e.g. voyage / text-embedding-3)
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 for b in h[:16]]


@router.post("/reviews/process")
async def process_review(payload: ProcessReviewRequest, request: Request):
    """
    Streams the processing pipeline back over SSE: classification result -> reply draft generated
    token-by-token -> final state.
    The frontend can consume it directly with EventSource, rendering as it receives data, without
    waiting for the whole pipeline to finish.

    The resulting draft is persisted and left in AWAITING_APPROVAL; a merchant closes it out via
    POST /reviews/{workflow_id}/decision.
    """
    app_state = request.app.state
    limiter_result = await app_state.rate_limiter.acquire(payload.tenant_id)
    if not limiter_result.allowed:
        raise HTTPException(status_code=429, detail="tenant rate limit exceeded, please retry shortly")

    workflow = ReviewWorkflow(app_state.llm_client, app_state.semantic_cache)
    ctx = WorkflowContext(tenant_id=payload.tenant_id, review=payload.review)
    embedding = _fake_embedding(payload.review.text)

    async def event_generator():
        async with repository_session(app_state.db_sessionmaker) as repo:
            await repo.create(ctx)

            async for ctx_snapshot, chunk in workflow.run(ctx, embedding):
                if not chunk:
                    # Only persist on state-transition checkpoints, not on every streamed token —
                    # otherwise a long draft would trigger one DB write per chunk.
                    await repo.sync_snapshot(ctx_snapshot)

                yield {
                    "event": "delta",
                    "data": json.dumps(
                        {
                            "state": ctx_snapshot.state,
                            "sentiment": ctx_snapshot.sentiment,
                            "delta": chunk,
                        }
                    ),
                }

        if payload.auto_notify_manager:
            await app_state.notifier.send(
                NotificationRequest(
                    tenant_id=payload.tenant_id,
                    channel=NotifyChannel.EMAIL,
                    recipient="manager@example.com",
                    subject=f"New review awaiting approval: {payload.review.review_id}",
                    body=ctx.reply_draft,
                    idempotency_key=f"{payload.review.review_id}:email",
                )
            )

        yield {
            "event": "done",
            "data": json.dumps({"workflow_id": ctx.workflow_id, "final_draft": ctx.reply_draft}),
        }

    return EventSourceResponse(event_generator())


@router.get("/reviews/pending", response_model=list[WorkflowRecordResponse])
async def list_pending_reviews(tenant_id: str, request: Request):
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        return await repo.list_pending(tenant_id)


@router.get("/reviews/{workflow_id}", response_model=WorkflowRecordResponse)
async def get_review_workflow(workflow_id: str, request: Request):
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        try:
            return await repo.get(workflow_id)
        except WorkflowNotFoundError:
            raise HTTPException(status_code=404, detail="workflow not found") from None


@router.post("/reviews/{workflow_id}/decision", response_model=WorkflowRecordResponse)
async def submit_review_decision(workflow_id: str, payload: WorkflowDecisionRequest, request: Request):
    """
    Records a merchant's approve/edit/reject decision on a drafted reply. This is the human-review
    gate: nothing here auto-publishes back to Yelp/Google yet — that's the next integration to add
    once a decision lands here as APPROVED.
    """
    async with repository_session(request.app.state.db_sessionmaker) as repo:
        try:
            return await repo.record_decision(
                workflow_id=workflow_id,
                decision=payload.decision,
                decided_by=payload.decided_by,
                edited_reply=payload.edited_reply,
            )
        except WorkflowNotFoundError:
            raise HTTPException(status_code=404, detail="workflow not found") from None
        except InvalidDecisionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
