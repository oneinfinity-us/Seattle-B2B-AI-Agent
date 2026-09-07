"""
Google OAuth: the single login mechanism for a merchant. One "Continue with Google" grant both
authenticates them (we read their email off the id token) and captures the refresh token their
GmailAccountCredential needs for Gmail/Calendar access — no separate email/password system.

`build_authorization_url` makes no network call and is unit-tested directly. `exchange_code` does
hit Google's token endpoint — like GmailProvider/GoogleCalendarProvider, it's exercised via the
`app.state.oauth_exchange` injection seam in tests, not against the live API.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@dataclass
class GoogleIdentity:
    email: str
    refresh_token: str
    access_token: str
    token_expiry: datetime | None
    scopes: list[str]


def _client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_authorization_url(client_id: str, client_secret: str, redirect_uri: str, state: str) -> str:
    # Imported lazily so google-auth-oauthlib is only required where OAuth is actually used, not for
    # every import of this module (e.g. under a stubbed app.state.oauth_exchange in tests).
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
    return auth_url


def _exchange_code_sync(client_id: str, client_secret: str, redirect_uri: str, code: str) -> GoogleIdentity:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token as google_id_token
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    claims = google_id_token.verify_oauth2_token(credentials.id_token, GoogleAuthRequest(), client_id)
    return GoogleIdentity(
        email=claims["email"],
        refresh_token=credentials.refresh_token,
        access_token=credentials.token,
        token_expiry=credentials.expiry,
        scopes=list(credentials.scopes or SCOPES),
    )


async def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> GoogleIdentity:
    # google-auth-oauthlib is synchronous; run the token exchange off the event loop.
    return await asyncio.to_thread(_exchange_code_sync, client_id, client_secret, redirect_uri, code)
