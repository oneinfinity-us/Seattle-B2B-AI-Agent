from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import GmailAccountCredential
from app.integrations.email_provider import GmailProvider


class _StubExecute:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _StubMessages:
    def __init__(self, list_result, get_results):
        self._list_result = list_result
        self._get_results = get_results

    def list(self, userId, q):  # noqa: N803 - matches the real googleapiclient call signature
        return _StubExecute(self._list_result)

    def get(self, userId, id, format):  # noqa: N803, A002 - same
        return _StubExecute(self._get_results[id])


class _StubUsers:
    def __init__(self, messages: _StubMessages):
        self._messages = messages

    def messages(self):
        return self._messages


class _StubService:
    def __init__(self, messages: _StubMessages):
        self._users = _StubUsers(messages)

    def users(self):
        return self._users


def _gmail_message(message_id: str, headers: dict[str, str], body_text: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "body": {"data": _b64(body_text)},
        },
    }


def _b64(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode()


def _make_provider(get_results: dict[str, dict]) -> GmailProvider:
    credential = GmailAccountCredential(
        tenant_id="owner@example.com",
        connected_email_address="owner@example.com",
        refresh_token="refresh",
        access_token="access",
        scopes="openid",
    )
    provider = GmailProvider(credential, client_id="client-id", client_secret="client-secret")
    stub_service = _StubService(_StubMessages({"messages": [{"id": mid} for mid in get_results]}, get_results))
    provider._build_service = lambda: stub_service  # bypass real OAuth/network for this unit test
    return provider


async def test_marks_bulk_mail_from_list_unsubscribe_header():
    get_results = {
        "m1": _gmail_message(
            "m1",
            {"From": "Netflix <info@members.netflix.com>", "Subject": "New for you", "List-Unsubscribe": "<mailto:x>"},
            "Check out this documentary.",
        )
    }
    provider = _make_provider(get_results)

    messages = await provider.list_recent_messages(datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert messages[0].is_bulk_mail is True
    assert messages[0].is_automated is False


async def test_marks_automated_from_auto_submitted_header():
    get_results = {
        "m1": _gmail_message(
            "m1",
            {"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Subject": "Undelivered", "Auto-Submitted": "auto-replied"},
            "Your message could not be delivered.",
        )
    }
    provider = _make_provider(get_results)

    messages = await provider.list_recent_messages(datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert messages[0].is_automated is True
    assert messages[0].is_bulk_mail is False


async def test_normal_email_has_neither_flag_set():
    get_results = {
        "m1": _gmail_message(
            "m1",
            {"From": "customer@example.com", "Subject": "Question about your hours"},
            "Are you open Sundays?",
        )
    }
    provider = _make_provider(get_results)

    messages = await provider.list_recent_messages(datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert messages[0].is_bulk_mail is False
    assert messages[0].is_automated is False
