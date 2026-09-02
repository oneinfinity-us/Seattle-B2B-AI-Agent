"""
通知服务：故意和"生成回复草稿"解耦成独立的异步任务。

为什么：
1. 发邮件/短信是外部 I/O，延迟不可控（可能几百 ms 到几秒），不应该阻塞 HTTP 请求或 SSE 流。
2. 需要幂等：同一条评论如果因为网络重试被处理两次，不能给商家发两条一模一样的短信。
   做法是用 idempotency_key（review_id + channel）写入 Redis SETNX，成功才真正发送。
3. 生产环境这里应该是 arq/Celery 的一个 worker task，由队列消费，而不是在请求线程里直接调用。
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
        # SETNX：只有第一次调用会成功拿到锁并真正发送，重复调用直接跳过
        acquired = await self._redis.set(idempotency_key, "1", nx=True, ex=60 * 60 * 24)
        if not acquired:
            logger.info("notification_skipped_duplicate", key=request.idempotency_key)
            return False

        # TODO: 接入 SendGrid / Twilio 真实 SDK
        logger.info(
            "notification_sent",
            channel=request.channel,
            recipient=request.recipient,
            tenant=request.tenant_id,
        )
        return True
