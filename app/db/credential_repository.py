from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GmailAccountCredential


class CredentialRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(
        self,
        *,
        tenant_id: str,
        business_name: str,
        refresh_token: str,
        access_token: str,
        token_expiry: datetime | None,
        scopes: list[str],
    ) -> GmailAccountCredential:
        # Checks existence via tenant_id alone (the primary key, never encrypted) instead of
        # `session.get(...)`, which would hydrate -- and therefore decrypt -- the refresh_token/
        # access_token we're about to overwrite anyway. That distinction matters the moment an old
        # row can't be decrypted under the current key (e.g. a pre-encryption plaintext row): reading
        # a value we're about to discard shouldn't be able to crash the login this method completes.
        exists = await self._session.scalar(
            select(GmailAccountCredential.tenant_id).where(GmailAccountCredential.tenant_id == tenant_id)
        )

        values = dict(
            business_name=business_name,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expiry=token_expiry,
            scopes=",".join(scopes),
        )

        if exists is None:
            self._session.add(GmailAccountCredential(tenant_id=tenant_id, connected_email_address=tenant_id, **values))
        else:
            await self._session.execute(
                update(GmailAccountCredential).where(GmailAccountCredential.tenant_id == tenant_id).values(**values)
            )

        await self._session.commit()
        return await self._session.get(GmailAccountCredential, tenant_id)

    async def get(self, tenant_id: str) -> GmailAccountCredential | None:
        return await self._session.get(GmailAccountCredential, tenant_id)
