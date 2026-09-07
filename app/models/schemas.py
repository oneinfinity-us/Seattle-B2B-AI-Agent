from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionKind(StrEnum):
    EMAIL_REPLY = "email_reply"
    SCHEDULING_PROPOSAL = "scheduling_proposal"


class ActionState(StrEnum):
    RECEIVED = "received"
    CLASSIFIED = "classified"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    FAILED = "failed"


class DecisionType(StrEnum):
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class EmailMessage(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime


class SyncRequest(BaseModel):
    auto_notify_manager: bool = True


class ActionDecisionRequest(BaseModel):
    """Submitted by a merchant to approve/edit/reject a drafted email action."""

    decision: DecisionType
    decided_by: str
    edited_reply: str | None = None
    reject_reason: str | None = None

    @model_validator(mode="after")
    def _edited_reply_required_when_editing(self) -> "ActionDecisionRequest":
        if self.decision == DecisionType.EDITED and not self.edited_reply:
            raise ValueError("edited_reply is required when decision is 'edited'")
        return self


class PendingActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    kind: ActionKind
    source_message_id: str
    reply_to: str
    summary: str
    state: ActionState
    draft_content: str
    final_content: str | None
    decision: DecisionType | None
    decided_by: str | None
    decided_at: datetime | None
    reject_reason: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


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
