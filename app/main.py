from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from redis.asyncio import Redis

from app.api.auth_routes import router as auth_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.llm_client import LLMClient
from app.core.rate_limiter import TenantRateLimiter
from app.core.sessions import SessionStore
from app.db import Base, create_engine_and_sessionmaker
from app.db.base import ensure_new_columns
from app.integrations import google_oauth
from app.integrations.calendar_provider import FakeCalendarProvider
from app.integrations.email_provider import FakeEmailProvider
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
    # create_all only creates brand-new tables — it never adds a column to one that already exists.
    # This patches up an already-provisioned database (e.g. Render's Postgres) for schema changes made
    # since it was first deployed. See ensure_new_columns' docstring for why this isn't Alembic.
    await ensure_new_columns(db_engine, Base)

    app.state.settings = settings
    app.state.redis = redis
    app.state.db_engine = db_engine
    app.state.db_sessionmaker = db_sessionmaker
    app.state.rate_limiter = TenantRateLimiter(
        redis, capacity=settings.rate_limit_capacity, refill_per_sec=settings.rate_limit_refill_per_sec
    )
    app.state.llm_client = LLMClient(settings)
    app.state.notifier = NotificationService(redis)
    app.state.sessions = SessionStore(redis, settings.session_ttl_seconds)
    app.state.oauth_exchange = google_oauth.exchange_code

    # Login now provisions a real GmailAccountCredential per tenant (app/api/auth_routes.py), but the
    # email/calendar actions used by the sync/draft pipeline still share one in-memory fake provider.
    # Swap this for a per-tenant GmailProvider/GoogleCalendarProvider lookup (built from that tenant's
    # credential row) to make the assistant act on the real connected inbox.
    app.state.email_provider = FakeEmailProvider()
    app.state.calendar_provider = FakeCalendarProvider()

    yield

    await redis.aclose()
    await db_engine.dispose()


app = FastAPI(title="Seattle B2B AI Assistant", lifespan=lifespan)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/privacy")
async def privacy():
    return FileResponse("app/static/privacy.html")


@app.get("/terms")
async def terms():
    return FileResponse("app/static/terms.html")
