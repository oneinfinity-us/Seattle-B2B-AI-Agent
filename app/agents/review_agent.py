"""
评论处理 Agent：用显式状态机而不是一坨 if/else 或单次 prompt 完成所有事情。

状态流转：
  RECEIVED -> CLASSIFIED -> DRAFTED -> AWAITING_APPROVAL -> APPROVED -> NOTIFIED
                                              -> REJECTED（商家可编辑或拒绝草稿）

为什么要这样设计（面试要点）：
1. 每个节点职责单一，可以单独测试、单独重试、单独观测延迟。
2. 状态必须持久化（这里用 Pydantic 模型 + 外部存储的接口，不是进程内变量），
   否则 worker 重启或者请求跨多次交互（比如等商家审核）时状态会丢失。
3. 差评（负面情绪）默认进入 AWAITING_APPROVAL 而不是自动发布 —— 这是产品/合规判断：
   AI 生成的道歉/解释类回复必须有人审核，防止品牌风险。
4. 生产环境建议直接换成 LangGraph 的 StateGraph：这里的 ReviewWorkflow 就是
   LangGraph 状态机的最小可用实现，好处是不引入额外依赖也能讲清楚原理。
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
        # 轻量骨架用关键词打分；生产环境这里应该是一次结构化输出的 LLM 调用
        # （用 response_format / tool_use 强制返回 {"sentiment": "..."}）。
        text = review.text.lower()
        negative_signals = ["slow", "rude", "cold", "disappointed", "bad", "差", "慢", "态度"]
        if any(sig in text for sig in negative_signals):
            return Sentiment.NEGATIVE
        return Sentiment.POSITIVE

    async def run(self, ctx: WorkflowContext, embedding: list[float]) -> AsyncGenerator[tuple[WorkflowContext, str], None]:
        """
        yield (当前上下文快照, 增量文本片段)，供 SSE 路由边生成边推给前端。
        """
        # 1. 分类
        ctx.sentiment = self._classify(ctx.review)
        ctx.state = WorkflowState.CLASSIFIED
        yield ctx, ""

        # 2. 语义缓存命中检查——省 token，也省延迟
        cache_hit = await self._cache.lookup(ctx.tenant_id, embedding)
        if cache_hit:
            ctx.reply_draft = cache_hit.reply_draft
            ctx.state = WorkflowState.DRAFTED
            yield ctx, ctx.reply_draft
        else:
            system = (
                "你是本地商户的客户沟通助手，语气专业、简洁、真诚。"
                f"当前评论情绪判断为：{ctx.sentiment}。"
                "如果是负面评论，先共情，再给出具体补救措施，不要找借口。"
            )
            draft_parts: list[str] = []
            async for chunk in self._llm.stream_reply_draft(system, ctx.review.text):
                draft_parts.append(chunk)
                yield ctx, chunk
            ctx.reply_draft = "".join(draft_parts)
            ctx.state = WorkflowState.DRAFTED
            await self._cache.store(ctx.tenant_id, ctx.review.review_id, embedding, ctx.reply_draft)

        # 3. 负面评论强制进入人工审核；正面评论可以允许"自动发布"策略（由商户在设置里开关）
        ctx.state = WorkflowState.AWAITING_APPROVAL
        yield ctx, ""
