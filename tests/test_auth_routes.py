from __future__ import annotations

from datetime import datetime, timezone

import fakeredis
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.auth_routes import router
from app.core.config import Settings
from app.core.sessions import SessionStore
from app.db.credential_repository import CredentialRepository
from app.integrations.google_oauth import GoogleIdentity

STUB_IDENTITY = GoogleIdentity(
    email="owner@bellavista.example",
    refresh_token="refresh-token-123",
    access_token="access-token-123",
    token_expiry=datetime.now(timezone.utc),
    scopes=["openid", "email"],
)


async def _stub_oauth_exchange(client_id, client_secret, redirect_uri, code):
    return STUB_IDENTITY


@pytest_asyncio.fixture
async def redis():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest_asyncio.fixture
async def client(db_sessionmaker, redis):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.settings = Settings(
        google_oauth_client_id="test-client-id",
        google_oauth_client_secret="test-client-secret",
        google_oauth_redirect_uri="http://testserver/api/v1/auth/google/callback",
        session_cookie_name="session",
        session_ttl_seconds=60,
    )
    app.state.redis = redis
    app.state.sessions = SessionStore(redis, ttl_seconds=60)
    app.state.db_sessionmaker = db_sessionmaker
    app.state.oauth_exchange = _stub_oauth_exchange

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as ac:
        yield ac


async def test_login_redirects_to_google_with_state(client):
    response = await client.get("/api/v1/auth/google/login")

    assert 300 <= response.status_code < 400
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "client_id=test-client-id" in location
    assert "state=" in location


async def test_callback_rejects_unknown_state(client):
    response = await client.get("/api/v1/auth/google/callback", params={"code": "fake-code", "state": "never-issued"})
    assert response.status_code == 400


async def test_callback_issues_session_and_persists_credential(client, redis, db_sessionmaker):
    await redis.set("oauth_state:test-state", "1", ex=600)

    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "fake-code", "state": "test-state", "business_name": "Bella Vista Trattoria"},
    )

    assert 300 <= response.status_code < 400
    session_id = response.cookies.get("session")
    assert session_id

    # The OAuth state is single-use.
    assert await redis.get("oauth_state:test-state") is None

    async with db_sessionmaker() as session:
        credential = await CredentialRepository(session).get("owner@bellavista.example")
    assert credential is not None
    assert credential.business_name == "Bella Vista Trattoria"
    assert credential.refresh_token == "refresh-token-123"


async def test_me_requires_a_valid_session(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_returns_profile_for_logged_in_tenant(client, redis, db_sessionmaker):
    await redis.set("oauth_state:test-state-2", "1", ex=600)
    callback = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "fake-code", "state": "test-state-2", "business_name": "Bella Vista Trattoria"},
    )
    client.cookies.set("session", callback.cookies["session"])

    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "owner@bellavista.example"
    assert body["business_name"] == "Bella Vista Trattoria"
    assert body["connected_email"] == "owner@bellavista.example"


async def test_logout_invalidates_the_session(client, redis):
    await redis.set("oauth_state:test-state-3", "1", ex=600)
    callback = await client.get(
        "/api/v1/auth/google/callback", params={"code": "fake-code", "state": "test-state-3"}
    )
    client.cookies.set("session", callback.cookies["session"])

    logout_response = await client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 401
