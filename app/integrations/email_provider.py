"""
Email provider abstraction. `GmailProvider` is the real implementation (Gmail API); `FakeEmailProvider`
is an in-memory stand-in used in tests and until a tenant has a real Google OAuth credential wired up.
Both satisfy `EmailProvider`, so the rest of the app never branches on which one it's talking to — the
same separation `LLMClient` already draws between "real Anthropic call" and what's actually testable.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from email.mime.text import MIMEText
from typing import Protocol

from app.db.models import GmailAccountCredential
from app.models.schemas import EmailMessage


class EmailProvider(Protocol):
    async def list_recent_messages(self, since: datetime) -> list[EmailMessage]: ...

    async def send_reply(self, thread_id: str, to: str, body: str) -> None: ...


class GmailProvider:
    """
    Real Gmail API implementation. Requires a `GmailAccountCredential` with a valid refresh token —
    see README's "Known Design Trade-offs" for how that credential is currently provisioned
    (out-of-band, not yet via a self-serve OAuth connect flow).
    """

    def __init__(self, credential: GmailAccountCredential, client_id: str, client_secret: str):
        self._credential = credential
        self._client_id = client_id
        self._client_secret = client_secret

    def _build_service(self):
        # Imported lazily so google-api-python-client is only required when a real Gmail credential
        # is actually in use, not for every import of this module (e.g. under FakeEmailProvider in tests).
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
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _list_recent_sync(self, since: datetime) -> list[EmailMessage]:
        service = self._build_service()
        # Gmail's search matches every label by default, including Sent — without `in:inbox` the
        # merchant's own outgoing replies come back as "new" messages on the next sync and the
        # assistant drafts a reply to itself.
        query = f"in:inbox after:{int(since.timestamp())}"
        result = service.users().messages().list(userId="me", q=query).execute()

        messages: list[EmailMessage] = []
        for item in result.get("messages", []):
            full = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
            headers = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
            messages.append(
                EmailMessage(
                    message_id=full["id"],
                    thread_id=full["threadId"],
                    sender=headers.get("From", ""),
                    subject=headers.get("Subject", ""),
                    body=_extract_plain_text_body(full["payload"]),
                    received_at=datetime.fromtimestamp(int(full["internalDate"]) / 1000),
                    is_bulk_mail="List-Unsubscribe" in headers,
                    is_automated=headers.get("Auto-Submitted", "no").lower() != "no",
                )
            )
        return messages

    def _send_reply_sync(self, thread_id: str, to: str, body: str) -> None:
        service = self._build_service()
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = "Re:"
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()

    async def list_recent_messages(self, since: datetime) -> list[EmailMessage]:
        # The Gmail client library is synchronous; run it off the event loop.
        return await asyncio.to_thread(self._list_recent_sync, since)

    async def send_reply(self, thread_id: str, to: str, body: str) -> None:
        await asyncio.to_thread(self._send_reply_sync, thread_id, to, body)


def _extract_plain_text_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="replace")
    for part in payload.get("parts", []):
        text = _extract_plain_text_body(part)
        if text:
            return text
    return ""


class FakeEmailProvider:
    """In-memory stand-in for tests and local dev before a tenant has a real Gmail credential wired up."""

    def __init__(self, messages: list[EmailMessage] | None = None):
        self.messages = messages or []
        self.sent: list[tuple[str, str, str]] = []  # (thread_id, to, body)

    async def list_recent_messages(self, since: datetime) -> list[EmailMessage]:
        return [m for m in self.messages if m.received_at >= since]

    async def send_reply(self, thread_id: str, to: str, body: str) -> None:
        self.sent.append((thread_id, to, body))
