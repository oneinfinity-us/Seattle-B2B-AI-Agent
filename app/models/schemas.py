from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class WorkflowState(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOTIFIED = "notified"


class DecisionType(StrEnum):
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


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


class WorkflowDecisionRequest(BaseModel):
    """Submitted by a merchant (e.g. via a one-click email link) to close out a drafted reply."""

    decision: DecisionType
    decided_by: str
    edited_reply: str | None = None

    @model_validator(mode="after")
    def _edited_reply_required_when_editing(self) -> "WorkflowDecisionRequest":
        if self.decision == DecisionType.EDITED and not self.edited_reply:
            raise ValueError("edited_reply is required when decision is 'edited'")
        return self


class WorkflowRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    review_id: str
    state: WorkflowState
    sentiment: Sentiment | None
    reply_draft: str
    final_reply: str | None
    decision: DecisionType | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
