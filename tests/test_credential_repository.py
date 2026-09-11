from __future__ import annotations

from app.db.credential_repository import CredentialRepository


async def test_list_all_tenant_ids_returns_every_onboarded_tenant(db_sessionmaker):
    async with db_sessionmaker() as session:
        repo = CredentialRepository(session)
        await repo.upsert(
            tenant_id="tenant-a@example.com", business_name="", refresh_token="r", access_token="a",
            token_expiry=None, scopes=["openid"],
        )
        await repo.upsert(
            tenant_id="tenant-b@example.com", business_name="", refresh_token="r", access_token="a",
            token_expiry=None, scopes=["openid"],
        )

        tenant_ids = await repo.list_all_tenant_ids()

    assert set(tenant_ids) == {"tenant-a@example.com", "tenant-b@example.com"}


async def test_list_all_tenant_ids_is_empty_when_no_one_has_connected(db_sessionmaker):
    async with db_sessionmaker() as session:
        tenant_ids = await CredentialRepository(session).list_all_tenant_ids()

    assert tenant_ids == []
