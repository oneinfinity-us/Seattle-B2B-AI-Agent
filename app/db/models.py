"""
ORM models. Enum columns use `values_callable` so the stored value is the StrEnum's actual string
(e.g. "awaiting_approval") rather than SQLAlchemy's default of the Python member name.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.schemas import ActionKind, ActionState, DecisionType


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_column(enum_cls: type) -> SAEnum:
    return SAEnum(enum_cls, values_callable=lambda cls: [member.value for member in cls])


class PendingActionRecord(Base):
    """Persisted state for one AI-drafted action (an email reply, or a scheduling proposal) awaiting
    human approval before it's sent."""

    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[ActionKind] = mapped_column(_enum_column(ActionKind))
    source_message_id: Mapped[str] = mapped_column(String(256), index=True)
    reply_to: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(String(512), default="")

    state: Mapped[ActionState] = mapped_column(_enum_column(ActionState), default=ActionState.RECEIVED)
    draft_content: Mapped[str] = mapped_column(Text, default="")
    final_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once, when the state first reaches AWAITING_APPROVAL. Distinct from updated_at, which gets
    # overwritten again at decision time — without this there'd be no way to separately measure
    # "how long drafting took" vs. "how long the merchant took to decide".
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    decision: Mapped[DecisionType | None] = mapped_column(_enum_column(DecisionType), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class GmailAccountCredential(Base):
    """
    One Google OAuth grant per tenant, covering both Gmail and Calendar scopes. Since a merchant logs
    in via the same Google grant (see app/api/auth_routes.py), tenant_id is the connected account's
    email address and this row doubles as the tenant record — there's no separate Tenant table.

    KNOWN GAP (MVP, not production-ready): tokens are stored in plaintext here. Before onboarding real
    customers this needs field-level encryption or a secrets manager (see README's "Known Design
    Trade-offs").
    """

    __tablename__ = "gmail_account_credentials"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    connected_email_address: Mapped[str] = mapped_column(String(256))
    business_name: Mapped[str] = mapped_column(String(256), default="")
    refresh_token: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str] = mapped_column(Text)  # comma-separated

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
