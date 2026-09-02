from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.routes import router
from app.core.cache import SemanticCache
from app.core.config import get_settings
from app.core.llm_client import LLMClient
from app.core.rate_limiter import TenantRateLimiter
from app.services.notifier import NotificationService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    在这里统一初始化所有需要连接池/长生命周期的对象（Redis、LLM client），
    绑定到 app.state 上供路由复用，避免每个请求都重新建立连接
    ——这是"如何做高并发下的资源复用"这类面试问题的直接答案。
    """
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    app.state.settings = settings
    app.state.redis = redis
    app.state.rate_limiter = TenantRateLimiter(
        redis, capacity=settings.rate_limit_capacity, refill_per_sec=settings.rate_limit_refill_per_sec
    )
    app.state.semantic_cache = SemanticCache(redis, settings.semantic_cache_similarity_threshold)
    app.state.llm_client = LLMClient(settings)
    app.state.notifier = NotificationService(redis)

    yield

    await redis.aclose()


app = FastAPI(title="Yelp Review AI Agent", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
