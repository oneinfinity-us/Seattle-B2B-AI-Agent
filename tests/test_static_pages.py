from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    # These routes are plain static-file responses that don't touch app.state, so the ASGI lifespan
    # (which would need a real Redis/DB) never needs to run for this test.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_index_serves_the_app_shell(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Inbox Copilot" in response.text


async def test_privacy_policy_is_served(client):
    response = await client.get("/privacy")
    assert response.status_code == 200
    assert "Privacy Policy" in response.text
    assert "OneInfinity LLC" in response.text


async def test_terms_of_service_is_served(client):
    response = await client.get("/terms")
    assert response.status_code == 200
    assert "Terms of Service" in response.text
