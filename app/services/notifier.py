"""
Notification service: deliberately decoupled from "generating the reply draft" into a separate
asynchronous task.

Why:
1. Sending email/SMS is external I/O with unpredictable latency (could be a few hundred ms to a few
   seconds), and should not block the HTTP request or the SSE stream.
2. Idempotency is required: if the same sync gets triggered twice due to a network retry, the
   merchant must not receive two identical digest emails. This is handled with an idempotency_key
   (e.g. tenant_id + date) written via Redis SETNX — only send for real once that succeeds. If the
   actual send then fails, the key is released so a later retry can still go out — otherwise a
   transient SendGrid outage would silently drop the notification forever.
3. In production, this should be a worker task under arq/Celery, consumed from a queue, rather than
   called directly in the request thread.
"""
from __future__ import annotations

import httpx
import structlog
from redis.asyncio import Redis

from app.models.schemas import NotificationRequest, NotifyChannel

logger = structlog.get_logger()

_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


class NotificationService:
    def __init__(self, redis: Redis, http_client: httpx.AsyncClient, sendgrid_api_key: str, from_email: str):
        self._redis = redis
        self._http = http_client
        self._sendgrid_api_key = sendgrid_api_key
        self._from_email = from_email

    async def send(self, request: NotificationRequest) -> bool:
        idempotency_key = f"notify:sent:{request.idempotency_key}"
        # SETNX: only the first call successfully acquires the lock and actually sends; repeat calls are skipped
        acquired = await self._redis.set(idempotency_key, "1", nx=True, ex=60 * 60 * 24)
        if not acquired:
            logger.info("notification_skipped_duplicate", key=request.idempotency_key)
            return False

        try:
            await self._deliver(request)
        except Exception:
            await self._redis.delete(idempotency_key)  # let a future retry actually attempt delivery
            logger.exception("notification_send_failed", recipient=request.recipient, tenant=request.tenant_id)
            return False

        logger.info("notification_sent", channel=request.channel, recipient=request.recipient, tenant=request.tenant_id)
        return True

    async def _deliver(self, request: NotificationRequest) -> None:
        if request.channel != NotifyChannel.EMAIL:
            raise NotImplementedError(f"{request.channel} notifications aren't implemented yet")
        if not self._sendgrid_api_key:
            raise RuntimeError("SENDGRID_API_KEY is not configured")

        response = await self._http.post(
            _SENDGRID_URL,
            headers={"Authorization": f"Bearer {self._sendgrid_api_key}"},
            json={
                "personalizations": [{"to": [{"email": request.recipient}]}],
                "from": {"email": self._from_email},
                "subject": request.subject,
                "content": [{"type": "text/plain", "value": request.body}],
            },
        )
        response.raise_for_status()
