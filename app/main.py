from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.routes import router
from app.core.cache import SemanticCache
from app.core.config import get_settings
from app.core.llm_client import LLMClient
from app.core.rate_limiter import TenantRateLimiter
from app.db import Base, create_engine_and_sessionmaker
from app.services.notifier import NotificationService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initializes all objects that need a connection pool/long lifetime (Redis, LLM client) here,
    binding them to app.state for routes to reuse, avoiding re-establishing a connection on every
    request — this is the direct answer to interview questions like "how do you handle resource reuse
    under high concurrency."
    """
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    db_engine, db_sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with db_engine.begin() as conn:
        # MVP: create tables directly rather than via Alembic migrations; revisit once the schema
        # needs to evolve without dropping data.
        await conn.run_sync(Base.metadata.create_all)

    app.state.settings = settings
    app.state.redis = redis
    app.state.db_engine = db_engine
    app.state.db_sessionmaker = db_sessionmaker
    app.state.rate_limiter = TenantRateLimiter(
        redis, capacity=settings.rate_limit_capacity, refill_per_sec=settings.rate_limit_refill_per_sec
    )
    app.state.semantic_cache = SemanticCache(redis, settings.semantic_cache_similarity_threshold)
    app.state.llm_client = LLMClient(settings)
    app.state.notifier = NotificationService(redis)

    yield

    await redis.aclose()
    await db_engine.dispose()


app = FastAPI(title="Yelp Review AI Agent", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
