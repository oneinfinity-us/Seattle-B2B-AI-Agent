from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from redis.asyncio import Redis
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.api.auth_routes import router as auth_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.llm_client import LLMClient
from app.core.rate_limiter import TenantRateLimiter
from app.core.sessions import SessionStore
from app.db import create_engine_and_sessionmaker
from app.integrations import google_oauth
from app.integrations.calendar_provider import FakeCalendarProvider
from app.integrations.email_provider import FakeEmailProvider
from app.services.notifier import NotificationService
from app.services.sync_service import sync_all_tenants

logger = structlog.get_logger()

_settings = get_settings()
if _settings.sentry_dsn:
    # Captures unhandled request exceptions (real 500s) automatically via the integrations below.
    # Background-task failures (the sync loop, notification delivery) aren't HTTP requests, so nothing
    # here sees them -- those call sentry_sdk.capture_exception() explicitly at their own try/except
    # sites instead (see _sync_loop below, sync_service.sync_all_tenants, notifier.NotificationService).
    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        environment=_settings.environment,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.0,  # error tracking only for now, not performance tracing
    )


async def _sync_loop(app_state, interval_seconds: float) -> None:
    """Runs sync_all_tenants forever on an interval. This is in-process and per-instance -- fine at
    the current single-instance scale, but if this ever runs on more than one instance, each one will
    duplicate-process every tenant (see README's "Known Design Trade-offs")."""
    while True:
        try:
            await sync_all_tenants(app_state)
        except Exception:  # noqa: BLE001 - a bad tick must not kill the loop
            sentry_sdk.capture_exception()
            logger.exception("scheduled_sync_loop_tick_failed")
        await asyncio.sleep(interval_seconds)


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
    http_client = httpx.AsyncClient(timeout=10.0)

    # Schema is Alembic's job now (`alembic upgrade head`, run before this process starts — see the
    # Dockerfile) — this used to also run Base.metadata.create_all()/ensure_new_columns() here, which
    # is exactly what caused a production 500 (create_all never alters an existing table).
    db_engine, db_sessionmaker = create_engine_and_sessionmaker(settings.database_url)

    app.state.settings = settings
    app.state.redis = redis
    app.state.db_engine = db_engine
    app.state.db_sessionmaker = db_sessionmaker
    app.state.rate_limiter = TenantRateLimiter(
        redis, capacity=settings.rate_limit_capacity, refill_per_sec=settings.rate_limit_refill_per_sec
    )
    app.state.llm_client = LLMClient(settings)
    app.state.http_client = http_client
    app.state.notifier = NotificationService(
        redis, http_client, settings.sendgrid_api_key, settings.notification_from_email
    )
    app.state.sessions = SessionStore(redis, settings.session_ttl_seconds)
    app.state.oauth_exchange = google_oauth.exchange_code

    # Shared fallback only — a logged-in tenant's real GmailProvider/GoogleCalendarProvider are built
    # per-request from their GmailAccountCredential (see app/integrations/provider_factory.py). This
    # pair only gets used for a tenant with no credential on file, which shouldn't happen in practice.
    app.state.email_provider = FakeEmailProvider()
    app.state.calendar_provider = FakeCalendarProvider()

    sync_task = asyncio.create_task(_sync_loop(app.state, settings.sync_interval_seconds))

    yield

    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass

    await redis.aclose()
    await db_engine.dispose()
    await http_client.aclose()


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
