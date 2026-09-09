"""
Email/calendar triage agent: an explicit state machine (same shape as the review agent this replaces)
instead of a single do-everything prompt.

State transitions:
  RECEIVED -> CLASSIFIED -> DRAFTED -> AWAITING_APPROVAL
  (then, outside this module: -> APPROVED/REJECTED -> SENT/FAILED, via PendingActionRepository)

Every drafted reply defaults to AWAITING_APPROVAL — nothing sends automatically. That's a
product/safety decision (an AI-drafted reply going out under the owner's name needs a human to see it
first), not a technical limitation.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.llm_client import LLMClient, TokenUsage
from app.integrations.calendar_provider import CalendarProvider
from app.models.schemas import ActionKind, ActionState, EmailMessage

_SCHEDULING_SIGNALS = ["schedule", "meet", "appointment", "available", "reschedule", "calendar"]
_SKIP_SENDERS = ["noreply", "no-reply", "notifications@", "newsletter", "mailer-daemon", "postmaster"]


class ActionContext(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    message: EmailMessage
    kind: ActionKind | None = None
    state: ActionState = ActionState.RECEIVED
    draft_content: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None


class AssistantWorkflow:
    def __init__(self, llm_client: LLMClient, calendar_provider: CalendarProvider):
        self._llm = llm_client
        self._calendar = calendar_provider

    def _classify(self, message: EmailMessage) -> ActionKind | None:
        # Header-based signals first (List-Unsubscribe / Auto-Submitted) — these come from real email
        # standards bulk senders and mail systems already comply with, so they catch things a sender
        # address never would (e.g. a marketing email from a normal-looking "info@" address).
        if message.is_bulk_mail or message.is_automated:
            return None

        # Cheap keyword fallback on the sender address for anything the headers didn't catch (or in
        # tests/dev where FakeEmailProvider messages don't set the header-derived flags).
        # TODO: swap for an LLM structured-output classification once there's a labeled email set to
        # validate against.
        sender = message.sender.lower()
        if any(skip in sender for skip in _SKIP_SENDERS):
            return None

        text = f"{message.subject} {message.body}".lower()
        if any(signal in text for signal in _SCHEDULING_SIGNALS):
            return ActionKind.SCHEDULING_PROPOSAL
        return ActionKind.EMAIL_REPLY

    async def run(self, ctx: ActionContext) -> AsyncGenerator[tuple[ActionContext, str], None]:
        """
        Yields (current context snapshot, incremental text chunk), mirroring the old review workflow's
        shape so the SSE route barely changes.
        """
        ctx.kind = self._classify(ctx.message)
        ctx.state = ActionState.CLASSIFIED
        yield ctx, ""

        if ctx.kind is None:
            # Not something the assistant should act on (e.g. an automated notification) — the
            # caller is expected to simply not persist/act on this context.
            return

        system = self._build_system_prompt(ctx.kind)
        if ctx.kind == ActionKind.SCHEDULING_PROPOSAL:
            open_slots = await self._describe_open_slots()
            prompt = f"{ctx.message.body}\n\nMy current availability: {open_slots}"
        else:
            prompt = ctx.message.body

        usage = TokenUsage()
        draft_parts: list[str] = []
        async for chunk in self._llm.stream_reply_draft(system, prompt, usage=usage):
            draft_parts.append(chunk)
            yield ctx, chunk

        ctx.draft_content = "".join(draft_parts)
        # Zero means the fallback path was used (no real completion billed), not "free" - leave it None.
        ctx.input_tokens = usage.input_tokens or None
        ctx.output_tokens = usage.output_tokens or None
        ctx.estimated_cost_usd = usage.estimated_cost_usd or None
        ctx.state = ActionState.DRAFTED
        yield ctx, ""

        ctx.state = ActionState.AWAITING_APPROVAL
        yield ctx, ""

    def _build_system_prompt(self, kind: ActionKind) -> str:
        if kind == ActionKind.SCHEDULING_PROPOSAL:
            return (
                "You are a scheduling assistant for a local business owner. Tone: professional and "
                "concise. Propose 2-3 concrete meeting times from the given availability, and ask the "
                "recipient to confirm one."
            )
        return (
            "You are an email assistant for a local business owner. Tone: professional, concise, and "
            "warm. Draft a reply to the email below that the owner can review and send as-is or edit."
        )

    async def _describe_open_slots(self) -> str:
        # v1: a simple textual summary derived from free/busy; real slot-finding logic (working hours,
        # meeting length, timezone) is a Phase B refinement once this loop is validated.
        now = datetime.now()
        busy = await self._calendar.get_free_busy(now, now + timedelta(days=3))
        if not busy:
            return "fully open for the next 3 days"
        return f"busy during {len(busy)} existing meeting(s) in the next 3 days; otherwise open"
