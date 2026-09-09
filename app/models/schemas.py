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
    # Derived from standard email headers (see GmailProvider): List-Unsubscribe marks bulk/marketing
    # mail (required on legitimate bulk senders since Gmail's 2024 sender rules), Auto-Submitted marks
    # bounces/autoresponders (RFC 3834). Far more reliable than guessing from the sender address.
    is_bulk_mail: bool = False
    is_automated: bool = False


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
    drafted_at: datetime | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    decision: DecisionType | None
    decided_by: str | None
    decided_at: datetime | None
    reject_reason: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class MetricsSummary(BaseModel):
    """Aggregated from PendingActionRecord — see PendingActionRepository.get_metrics. Rates/averages
    are None (not 0) when their denominator is zero, so the frontend can render "not enough data yet"
    instead of a misleading 0%."""

    total_actions: int
    pending_count: int
    sent_count: int
    rejected_count: int
    failed_count: int
    approved_without_edits_count: int
    edited_before_send_count: int
    override_rate: float | None
    rejection_rate: float | None
    avg_draft_seconds: float | None
    avg_decision_seconds: float | None
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    avg_cost_per_action: float | None


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
