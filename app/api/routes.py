from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.agents.review_agent import ReviewWorkflow, WorkflowContext
from app.models.schemas import NotificationRequest, NotifyChannel, ProcessReviewRequest

router = APIRouter()


def _fake_embedding(text: str) -> list[float]:
    # 骨架占位：生产环境换成真实的 embedding 模型调用（如 voyage / text-embedding-3）
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 for b in h[:16]]


@router.post("/reviews/process")
async def process_review(payload: ProcessReviewRequest, request: Request):
    """
    SSE 流式返回处理过程：分类结果 -> 逐字生成的回复草稿 -> 最终状态。
    前端可以直接用 EventSource 消费，边收边渲染，不需要等整个流程跑完。
    """
    app_state = request.app.state
    limiter_result = await app_state.rate_limiter.acquire(payload.tenant_id)
    if not limiter_result.allowed:
        raise HTTPException(status_code=429, detail="tenant rate limit exceeded, please retry shortly")

    workflow = ReviewWorkflow(app_state.llm_client, app_state.semantic_cache)
    ctx = WorkflowContext(tenant_id=payload.tenant_id, review=payload.review)
    embedding = _fake_embedding(payload.review.text)

    async def event_generator():
        async for ctx_snapshot, chunk in workflow.run(ctx, embedding):
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
                    subject=f"新评论待审核：{payload.review.review_id}",
                    body=ctx.reply_draft,
                    idempotency_key=f"{payload.review.review_id}:email",
                )
            )

        yield {"event": "done", "data": json.dumps({"final_draft": ctx.reply_draft})}

    return EventSourceResponse(event_generator())
