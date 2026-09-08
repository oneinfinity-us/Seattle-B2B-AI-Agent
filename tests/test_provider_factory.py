from __future__ import annotations

from types import SimpleNamespace

from app.core.config import Settings
from app.db.credential_repository import CredentialRepository
from app.integrations.calendar_provider import FakeCalendarProvider, GoogleCalendarProvider
from app.integrations.email_provider import FakeEmailProvider, GmailProvider
from app.integrations.provider_factory import build_providers_for_tenant


def _make_app_state(db_sessionmaker):
    return SimpleNamespace(
        db_sessionmaker=db_sessionmaker,
        settings=Settings(google_oauth_client_id="client-id", google_oauth_client_secret="client-secret"),
        email_provider=FakeEmailProvider(),
        calendar_provider=FakeCalendarProvider(),
    )


async def test_falls_back_to_shared_fakes_when_no_credential_on_file(db_sessionmaker):
    app_state = _make_app_state(db_sessionmaker)

    email_provider, calendar_provider = await build_providers_for_tenant(app_state, "owner@example.com")

    assert email_provider is app_state.email_provider
    assert calendar_provider is app_state.calendar_provider


async def test_builds_real_providers_when_credential_exists(db_sessionmaker):
    app_state = _make_app_state(db_sessionmaker)
    async with db_sessionmaker() as session:
        await CredentialRepository(session).upsert(
            tenant_id="owner@example.com",
            business_name="Bella Vista Trattoria",
            refresh_token="refresh-123",
            access_token="access-123",
            token_expiry=None,
            scopes=["openid", "email"],
        )

    email_provider, calendar_provider = await build_providers_for_tenant(app_state, "owner@example.com")

    assert isinstance(email_provider, GmailProvider)
    assert isinstance(calendar_provider, GoogleCalendarProvider)
