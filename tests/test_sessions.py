from __future__ import annotations

import fakeredis
import pytest_asyncio

from app.core.sessions import SessionStore


@pytest_asyncio.fixture
async def store():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return SessionStore(redis, ttl_seconds=60)


async def test_create_then_get_returns_tenant_id(store):
    session_id = await store.create("owner@bellavista.example")

    assert await store.get_tenant_id(session_id) == "owner@bellavista.example"


async def test_get_returns_none_for_unknown_session(store):
    assert await store.get_tenant_id("does-not-exist") is None


async def test_delete_invalidates_the_session(store):
    session_id = await store.create("owner@bellavista.example")

    await store.delete(session_id)

    assert await store.get_tenant_id(session_id) is None


async def test_each_session_id_is_unique():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = SessionStore(redis, ttl_seconds=60)

    first = await store.create("a@example.com")
    second = await store.create("b@example.com")

    assert first != second
    assert await store.get_tenant_id(first) == "a@example.com"
    assert await store.get_tenant_id(second) == "b@example.com"
