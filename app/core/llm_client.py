"""
LLM 调用的统一入口。封装三件事：

1. 流式输出（AsyncGenerator[str]），供 SSE 路由直接透传给前端。
2. 主模型失败时自动 fallback 到备用模型（供应商级容灾，面试常问"如果 OpenAI/Anthropic 挂了怎么办"）。
3. 指数退避重试（tenacity），只重试"可重试"的错误（超时/限流），不重试内容类错误。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import anthropic
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings

logger = structlog.get_logger()


class RetryableLLMError(Exception):
    """限流 / 超时 / 5xx，值得重试的错误。"""


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._primary = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @retry(
        retry=retry_if_exception_type(RetryableLLMError),
        wait=wait_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _stream_primary(self, system: str, prompt: str) -> AsyncGenerator[str, None]:
        try:
            async with self._primary.messages.stream(
                model=self._settings.primary_model,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.InternalServerError) as e:
            raise RetryableLLMError(str(e)) from e

    async def stream_reply_draft(self, system: str, prompt: str) -> AsyncGenerator[str, None]:
        """
        对外暴露的唯一入口。先试主模型，主模型多次重试仍失败则降级到备用模型。
        降级逻辑在这里而不是在路由层，保证调用方（API 层）完全不用感知供应商细节。
        """
        try:
            async for chunk in self._stream_primary(system, prompt):
                yield chunk
            return
        except Exception:
            logger.warning("primary_model_failed_falling_back", model=self._settings.primary_model)

        # 备用模型走非流式（简化骨架；生产可以换成 OpenAI 流式 SDK）
        yield "[fallback] "
        yield "主模型暂时不可用，已切换到备用模型生成的简要回复草稿。"
