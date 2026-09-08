"""
Builds the real per-tenant EmailProvider/CalendarProvider from that tenant's stored
GmailAccountCredential, so the sync/draft/send pipeline acts on the merchant's actual connected
inbox instead of the shared in-memory fakes app/main.py wires up by default.

Falls back to the shared fakes when a tenant has no credential on file — shouldn't happen for a
tenant who logged in via Google (app/api/auth_routes.py always upserts one), but keeps local
dev/tests that bypass login working without a special case.
"""
from __future__ import annotations

from typing import Any

from app.db.credential_repository import CredentialRepository
from app.integrations.calendar_provider import CalendarProvider, GoogleCalendarProvider
from app.integrations.email_provider import EmailProvider, GmailProvider


async def build_providers_for_tenant(app_state: Any, tenant_id: str) -> tuple[EmailProvider, CalendarProvider]:
    async with app_state.db_sessionmaker() as session:
        credential = await CredentialRepository(session).get(tenant_id)

    if credential is None:
        return app_state.email_provider, app_state.calendar_provider

    settings = app_state.settings
    email_provider = GmailProvider(credential, settings.google_oauth_client_id, settings.google_oauth_client_secret)
    calendar_provider = GoogleCalendarProvider(
        credential, settings.google_oauth_client_id, settings.google_oauth_client_secret
    )
    return email_provider, calendar_provider
