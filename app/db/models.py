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
from app.models.schemas import DecisionType, Sentiment, WorkflowState


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_column(enum_cls: type) -> SAEnum:
    return SAEnum(enum_cls, values_callable=lambda cls: [member.value for member in cls])


class ReviewWorkflowRecord(Base):
    """Persisted state for one review's classify -> draft -> human-approval workflow."""

    __tablename__ = "review_workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    review_id: Mapped[str] = mapped_column(String(128), index=True)
    review_text: Mapped[str] = mapped_column(Text)

    sentiment: Mapped[Sentiment | None] = mapped_column(_enum_column(Sentiment), nullable=True)
    state: Mapped[WorkflowState] = mapped_column(_enum_column(WorkflowState), default=WorkflowState.RECEIVED)
    reply_draft: Mapped[str] = mapped_column(Text, default="")
    final_reply: Mapped[str | None] = mapped_column(Text, nullable=True)

    decision: Mapped[DecisionType | None] = mapped_column(_enum_column(DecisionType), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
