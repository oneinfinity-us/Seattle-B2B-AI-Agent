from enum import StrEnum

from pydantic import BaseModel, Field


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class ReviewInput(BaseModel):
    review_id: str
    author: str
    rating: int = Field(ge=1, le=5)
    text: str


class ProcessReviewRequest(BaseModel):
    tenant_id: str
    review: ReviewInput
    auto_notify_manager: bool = True


class NotifyChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class NotificationRequest(BaseModel):
    tenant_id: str
    channel: NotifyChannel
    recipient: str
    subject: str
    body: str
    idempotency_key: str
