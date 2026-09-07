"""
Google OAuth login: a merchant's only sign-in method. One "Continue with Google" grant both
authenticates them and captures the refresh token app/integrations/email_provider.py and
calendar_provider.py need for Gmail/Calendar access (see GmailAccountCredential in app/db/models.py).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.core.sessions import require_tenant
from app.db.credential_repository import CredentialRepository

router = APIRouter()

_OAUTH_STATE_TTL_SECONDS = 600


@router.get("/auth/google/login")
async def google_login(request: Request):
    settings = request.app.state.settings
    state = secrets.token_urlsafe(24)
    # Consumed exactly once in the callback (via GETDEL) to prevent CSRF/replay of the OAuth redirect.
    await request.app.state.redis.set(f"oauth_state:{state}", "1", ex=_OAUTH_STATE_TTL_SECONDS)

    from app.integrations.google_oauth import build_authorization_url

    auth_url = build_authorization_url(
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_redirect_uri,
        state,
    )
    return RedirectResponse(auth_url)


@router.get("/auth/google/callback")
async def google_callback(code: str, state: str, request: Request, business_name: str = ""):
    app_state = request.app.state
    settings = app_state.settings

    consumed = await app_state.redis.getdel(f"oauth_state:{state}")
    if not consumed:
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")

    identity = await app_state.oauth_exchange(
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_redirect_uri,
        code,
    )

    async with app_state.db_sessionmaker() as session:
        repo = CredentialRepository(session)
        await repo.upsert(
            tenant_id=identity.email,
            business_name=business_name or identity.email,
            refresh_token=identity.refresh_token,
            access_token=identity.access_token,
            token_expiry=identity.token_expiry,
            scopes=identity.scopes,
        )

    session_id = await app_state.sessions.create(identity.email)
    # "/" is a placeholder until a real frontend dashboard exists to redirect a freshly-logged-in
    # merchant to.
    response = RedirectResponse(url="/")
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    app_state = request.app.state
    settings = app_state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        await app_state.sessions.delete(session_id)
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request, tenant_id: str = Depends(require_tenant)):
    async with request.app.state.db_sessionmaker() as session:
        credential = await CredentialRepository(session).get(tenant_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="no credential on file for this session")
    return {
        "tenant_id": tenant_id,
        "business_name": credential.business_name,
        "connected_email": credential.connected_email_address,
    }
