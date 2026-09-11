from __future__ import annotations

import json

import fakeredis
import httpx
import sentry_sdk

from app.models.schemas import NotificationRequest, NotifyChannel
from app.services.notifier import NotificationService


def _make_request(**overrides) -> NotificationRequest:
    defaults = dict(
        tenant_id="owner@example.com",
        channel=NotifyChannel.EMAIL,
        recipient="owner@example.com",
        subject="3 item(s) awaiting your review",
        body="Log in to review AI-drafted replies awaiting your approval.",
        idempotency_key="owner@example.com:2026-01-01:digest",
    )
    defaults.update(overrides)
    return NotificationRequest(**defaults)


def _make_service(handler, sendgrid_api_key: str = "test-key", from_email: str = "notifications@example.com"):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NotificationService(redis, http_client, sendgrid_api_key, from_email), redis, http_client


async def test_send_posts_the_expected_payload_to_sendgrid():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    service, _redis, http_client = _make_service(handler)
    try:
        result = await service.send(_make_request())
    finally:
        await http_client.aclose()

    assert result is True
    assert len(captured) == 1
    sent = captured[0]
    assert sent.url == "https://api.sendgrid.com/v3/mail/send"
    assert sent.headers["authorization"] == "Bearer test-key"
    body = json.loads(sent.content)
    assert body["personalizations"][0]["to"][0]["email"] == "owner@example.com"
    assert body["from"]["email"] == "notifications@example.com"
    assert body["subject"] == "3 item(s) awaiting your review"


async def test_duplicate_idempotency_key_is_skipped_without_calling_sendgrid():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(202)

    service, _redis, http_client = _make_service(handler)
    try:
        first = await service.send(_make_request())
        second = await service.send(_make_request())
    finally:
        await http_client.aclose()

    assert first is True
    assert second is False
    assert len(calls) == 1


async def test_sendgrid_failure_releases_the_idempotency_key_for_a_retry():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        # First attempt: SendGrid is down. Second attempt (the retry): it has recovered.
        return httpx.Response(500 if call_count["n"] == 1 else 202)

    service, _redis, http_client = _make_service(handler)
    try:
        first = await service.send(_make_request())
        assert first is False
        assert call_count["n"] == 1

        # The retry should actually attempt delivery, not think it already sent.
        second = await service.send(_make_request())
        assert second is True
        assert call_count["n"] == 2
    finally:
        await http_client.aclose()


async def test_send_fails_without_crashing_when_sendgrid_api_key_missing(monkeypatch):
    captures = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda *a, **k: captures.append(1))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never call SendGrid without an API key")

    service, _redis, http_client = _make_service(handler, sendgrid_api_key="")
    try:
        result = await service.send(_make_request())
    finally:
        await http_client.aclose()

    assert result is False
    # Not configuring SendGrid yet is a known, expected state -- not something to page anyone for.
    assert captures == []


async def test_sms_channel_is_not_yet_implemented(monkeypatch):
    captures = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda *a, **k: captures.append(1))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("SMS should never reach an HTTP call yet")

    service, _redis, http_client = _make_service(handler)
    try:
        result = await service.send(_make_request(channel=NotifyChannel.SMS, recipient="+15555550100"))
    finally:
        await http_client.aclose()

    assert result is False
    assert captures == []


async def test_a_genuine_delivery_failure_is_reported_to_sentry(monkeypatch):
    captures = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda *a, **k: captures.append(1))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    service, _redis, http_client = _make_service(handler)
    try:
        result = await service.send(_make_request())
    finally:
        await http_client.aclose()

    assert result is False
    # Unlike "not configured yet", an actual SendGrid failure is worth knowing about.
    assert captures == [1]
