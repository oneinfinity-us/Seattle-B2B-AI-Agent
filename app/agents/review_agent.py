"""
Review-processing Agent: uses an explicit state machine instead of a pile of if/else or a single prompt
that does everything.

State transitions:
  RECEIVED -> CLASSIFIED -> DRAFTED -> AWAITING_APPROVAL -> APPROVED -> NOTIFIED
                                              -> REJECTED (the merchant can edit or reject the draft)

Why it's designed this way (interview talking points):
1. Each node has a single responsibility, so it can be tested, retried, and have its latency observed
   independently.
2. State must be persisted (here, a Pydantic model + an interface to external storage, not an
   in-process variable) — otherwise state would be lost on a worker restart or when a request spans
   multiple interactions (e.g., waiting for merchant approval).
3. Negative reviews default to entering AWAITING_APPROVAL rather than being auto-published — this is a
   product/compliance decision: AI-generated apology/explanation replies must be reviewed by a human to
   prevent brand risk.
4. In production, this should be swapped directly for LangGraph's StateGraph: the ReviewWorkflow here is
   the minimal viable implementation of a LangGraph-style state machine, with the benefit that it can
   explain the principle without adding an extra dependency.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import StrEnum

from pydantic import BaseModel

from app.core.cache import SemanticCache
from app.core.llm_client import LLMClient
from app.models.schemas import ReviewInput, Sentiment


class WorkflowState(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    NOTIFIED = "notified"


class WorkflowContext(BaseModel):
    tenant_id: str
    review: ReviewInput
    state: WorkflowState = WorkflowState.RECEIVED
    sentiment: Sentiment | None = None
    reply_draft: str = ""


class ReviewWorkflow:
    def __init__(self, llm_client: LLMClient, semantic_cache: SemanticCache):
        self._llm = llm_client
        self._cache = semantic_cache

    def _classify(self, review: ReviewInput) -> Sentiment:
        # The lightweight skeleton uses keyword scoring; in production this should be a structured-output
        # LLM call (using response_format / tool_use to force a return of {"sentiment": "..."}).
        text = review.text.lower()
        negative_signals = ["slow", "rude", "cold", "disappointed", "bad", "attitude"]
        if any(sig in text for sig in negative_signals):
            return Sentiment.NEGATIVE
        return Sentiment.POSITIVE

    async def run(self, ctx: WorkflowContext, embedding: list[float]) -> AsyncGenerator[tuple[WorkflowContext, str], None]:
        """
        Yields (current context snapshot, incremental text chunk), for the SSE route to push to the
        frontend as it's generated.
        """
        # 1. Classify
        ctx.sentiment = self._classify(ctx.review)
        ctx.state = WorkflowState.CLASSIFIED
        yield ctx, ""

        # 2. Check for a semantic cache hit — saves tokens, and saves latency
        cache_hit = await self._cache.lookup(ctx.tenant_id, embedding)
        if cache_hit:
            ctx.reply_draft = cache_hit.reply_draft
            ctx.state = WorkflowState.DRAFTED
            yield ctx, ctx.reply_draft
        else:
            system = (
                "You are a customer-communication assistant for a local business. Tone: professional, "
                "concise, and sincere. "
                f"The current review's sentiment has been classified as: {ctx.sentiment}. "
                "If it's a negative review, lead with empathy, then offer a concrete remedy — do not make "
                "excuses."
            )
            draft_parts: list[str] = []
            async for chunk in self._llm.stream_reply_draft(system, ctx.review.text):
                draft_parts.append(chunk)
                yield ctx, chunk
            ctx.reply_draft = "".join(draft_parts)
            ctx.state = WorkflowState.DRAFTED
            await self._cache.store(ctx.tenant_id, ctx.review.review_id, embedding, ctx.reply_draft)

        # 3. Negative reviews are forced into human review; positive reviews may allow an "auto-publish"
        # policy (toggled by the merchant in settings)
        ctx.state = WorkflowState.AWAITING_APPROVAL
        yield ctx, ""
