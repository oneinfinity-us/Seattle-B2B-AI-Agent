"""
Calendar provider abstraction — read-only for v1 (free/busy only, no event creation/booking yet; see
README's "Known Design Trade-offs"). `GoogleCalendarProvider` is the real implementation;
`FakeCalendarProvider` is an in-memory stand-in for tests and local dev.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from app.db.models import GmailAccountCredential


class CalendarProvider(Protocol):
    async def get_free_busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Returns the list of busy intervals in [start, end); everything else is assumed free."""
        ...


class GoogleCalendarProvider:
    def __init__(self, credential: GmailAccountCredential, client_id: str, client_secret: str):
        self._credential = credential
        self._client_id = client_id
        self._client_secret = client_secret

    def _build_service(self):
        # Imported lazily, same reasoning as GmailProvider: only needed when a real credential is in use.
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=self._credential.access_token,
            refresh_token=self._credential.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=self._credential.scopes.split(","),
        )
        if creds.expired:
            creds.refresh(Request())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _get_free_busy_sync(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        service = self._build_service()
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]}
        result = service.freebusy().query(body=body).execute()
        busy = result["calendars"]["primary"]["busy"]
        return [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy]

    async def get_free_busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return await asyncio.to_thread(self._get_free_busy_sync, start, end)


class FakeCalendarProvider:
    def __init__(self, busy_intervals: list[tuple[datetime, datetime]] | None = None):
        self.busy_intervals = busy_intervals or []

    async def get_free_busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return [
            (busy_start, busy_end)
            for busy_start, busy_end in self.busy_intervals
            if busy_end > start and busy_start < end
        ]
