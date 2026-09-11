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
