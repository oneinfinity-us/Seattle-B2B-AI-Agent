"""
Notification service: deliberately decoupled from "generating the reply draft" into a separate
asynchronous task.

Why:
1. Sending email/SMS is external I/O with unpredictable latency (could be a few hundred ms to a few
   seconds), and should not block the HTTP request or the SSE stream.
2. Idempotency is required: if the same review gets processed twice due to a network retry, the
   merchant must not receive two identical text messages. This is handled with an idempotency_key
   (review_id + channel) written via Redis SETNX — only send for real once that succeeds.
3. In production, this should be a worker task under arq/Celery, consumed from a queue, rather than
   called directly in the request thread.
"""
from __future__ import annotations

import structlog
from redis.asyncio import Redis

from app.models.schemas import NotificationRequest

logger = structlog.get_logger()


class NotificationService:
    def __init__(self, redis: Redis):
        self._redis = redis

    async def send(self, request: NotificationRequest) -> bool:
        idempotency_key = f"notify:sent:{request.idempotency_key}"
        # SETNX: only the first call successfully acquires the lock and actually sends; repeat calls are skipped
        acquired = await self._redis.set(idempotency_key, "1", nx=True, ex=60 * 60 * 24)
        if not acquired:
            logger.info("notification_skipped_duplicate", key=request.idempotency_key)
            return False

        # TODO: integrate the real SendGrid / Twilio SDK
        logger.info(
            "notification_sent",
            channel=request.channel,
            recipient=request.recipient,
            tenant=request.tenant_id,
        )
        return True
