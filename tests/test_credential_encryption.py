from __future__ import annotations

from sqlalchemy import text

from app.db.credential_repository import CredentialRepository


async def test_refresh_and_access_tokens_are_encrypted_at_rest(db_sessionmaker):
    async with db_sessionmaker() as session:
        await CredentialRepository(session).upsert(
            tenant_id="owner@example.com",
            business_name="Bella Vista Trattoria",
            refresh_token="super-secret-refresh-token",
            access_token="super-secret-access-token",
            token_expiry=None,
            scopes=["openid", "email"],
        )

    # Raw SQL bypasses the ORM's EncryptedText type decoration, so this reads exactly what's on disk.
    async with db_sessionmaker() as session:
        result = await session.execute(
            text("SELECT refresh_token, access_token FROM gmail_account_credentials WHERE tenant_id = :t"),
            {"t": "owner@example.com"},
        )
        raw_refresh_token, raw_access_token = result.one()

    assert "super-secret-refresh-token" not in raw_refresh_token
    assert "super-secret-access-token" not in raw_access_token

    # ...but the repository (going through the ORM) still sees plaintext, transparently.
    async with db_sessionmaker() as session:
        credential = await CredentialRepository(session).get("owner@example.com")
    assert credential.refresh_token == "super-secret-refresh-token"
    assert credential.access_token == "super-secret-access-token"


async def test_upsert_overwrites_a_row_with_undecryptable_data_instead_of_crashing(db_sessionmaker):
    # Reproduces the real incident: a row written before encryption was added (plain, non-Fernet text)
    # must not block a merchant from logging back in and getting a freshly encrypted row.
    async with db_sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO gmail_account_credentials "
                "(tenant_id, connected_email_address, business_name, refresh_token, access_token, scopes, "
                "created_at, updated_at) "
                "VALUES (:t, :t, '', 'plaintext-refresh-token', 'plaintext-access-token', 'openid', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"t": "owner@example.com"},
        )
        await session.commit()

    async with db_sessionmaker() as session:
        record = await CredentialRepository(session).upsert(
            tenant_id="owner@example.com",
            business_name="Bella Vista Trattoria",
            refresh_token="fresh-refresh-token",
            access_token="fresh-access-token",
            token_expiry=None,
            scopes=["openid", "email"],
        )

    assert record.refresh_token == "fresh-refresh-token"
    assert record.access_token == "fresh-access-token"

    async with db_sessionmaker() as session:
        reloaded = await CredentialRepository(session).get("owner@example.com")
    assert reloaded.refresh_token == "fresh-refresh-token"
