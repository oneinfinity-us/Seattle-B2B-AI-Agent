from __future__ import annotations

from datetime import datetime

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
        record = await self._session.get(GmailAccountCredential, tenant_id)
        if record is None:
            record = GmailAccountCredential(tenant_id=tenant_id, connected_email_address=tenant_id)
            self._session.add(record)

        record.business_name = business_name
        record.refresh_token = refresh_token
        record.access_token = access_token
        record.token_expiry = token_expiry
        record.scopes = ",".join(scopes)

        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def get(self, tenant_id: str) -> GmailAccountCredential | None:
        return await self._session.get(GmailAccountCredential, tenant_id)
