"""
Redis-backed server-side sessions, issued after a successful Google login (see
app/api/auth_routes.py). A session_id (an opaque random cookie value) maps to a tenant_id — the
connected Google account's email address — so the browser never carries the tenant_id itself.

Mirrors the style of TenantRateLimiter/NotificationService: a thin async wrapper around the Redis
connection already shared via app.state.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from redis.asyncio import Redis


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def create(self, tenant_id: str) -> str:
        session_id = secrets.token_urlsafe(32)
        await self._redis.set(f"session:{session_id}", tenant_id, ex=self._ttl_seconds)
        return session_id

    async def get_tenant_id(self, session_id: str) -> str | None:
        return await self._redis.get(f"session:{session_id}")

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(f"session:{session_id}")


async def require_tenant(request: Request) -> str:
    """FastAPI dependency: resolves tenant_id from the session cookie, or 401s. Every route that
    acts on a tenant's data must depend on this instead of accepting a client-supplied tenant_id —
    otherwise any caller could read or act on another tenant's data just by knowing their id."""
    app_state = request.app.state
    session_id = request.cookies.get(app_state.settings.session_cookie_name)
    tenant_id = session_id and await app_state.sessions.get_tenant_id(session_id)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="not logged in")
    return tenant_id
