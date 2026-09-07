from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

from app.agents.assistant_agent import ActionContext, AssistantWorkflow
from app.integrations.calendar_provider import FakeCalendarProvider
from app.models.schemas import ActionKind, ActionState, EmailMessage


class StubLLMClient:
    """Duck-types LLMClient.stream_reply_draft without making a real Anthropic call."""

    def __init__(self, chunks: list[str] | None = None):
        self.chunks = chunks or ["Thanks for reaching out, ", "we'll get back to you soon."]
        self.received_prompts: list[tuple[str, str]] = []

    async def stream_reply_draft(self, system: str, prompt: str) -> AsyncGenerator[str, None]:
        self.received_prompts.append((system, prompt))
        for chunk in self.chunks:
            yield chunk


def _make_message(**overrides) -> EmailMessage:
    defaults = dict(
        message_id="msg-1",
        thread_id="thread-1",
        sender="customer@example.com",
        subject="Question about your hours",
        body="Are you open on Sundays?",
        received_at=datetime.now(),
    )
    defaults.update(overrides)
    return EmailMessage(**defaults)


async def _run_to_completion(workflow: AssistantWorkflow, ctx: ActionContext) -> ActionContext:
    last = ctx
    async for snapshot, _chunk in workflow.run(ctx):
        last = snapshot
    return last


async def test_skips_automated_sender():
    llm = StubLLMClient()
    workflow = AssistantWorkflow(llm, FakeCalendarProvider())
    ctx = ActionContext(tenant_id="tenant-1", message=_make_message(sender="notifications@yelp.com"))

    final = await _run_to_completion(workflow, ctx)

    assert final.kind is None
    assert llm.received_prompts == []


async def test_classifies_and_drafts_plain_reply():
    llm = StubLLMClient(["Sure, ", "we're open every Sunday from 10am-4pm."])
    workflow = AssistantWorkflow(llm, FakeCalendarProvider())
    ctx = ActionContext(tenant_id="tenant-1", message=_make_message())

    final = await _run_to_completion(workflow, ctx)

    assert final.kind == ActionKind.EMAIL_REPLY
    assert final.state == ActionState.AWAITING_APPROVAL
    assert final.draft_content == "Sure, we're open every Sunday from 10am-4pm."
    assert len(llm.received_prompts) == 1


async def test_classifies_scheduling_intent_and_includes_availability_in_prompt():
    now = datetime.now()
    busy = [(now + timedelta(hours=1), now + timedelta(hours=2))]
    llm = StubLLMClient(["How about Tuesday at 2pm?"])
    workflow = AssistantWorkflow(llm, FakeCalendarProvider(busy))
    message = _make_message(subject="Can we schedule a meeting?", body="Are you available this week to meet?")
    ctx = ActionContext(tenant_id="tenant-1", message=message)

    final = await _run_to_completion(workflow, ctx)

    assert final.kind == ActionKind.SCHEDULING_PROPOSAL
    assert final.state == ActionState.AWAITING_APPROVAL
    system, prompt = llm.received_prompts[0]
    assert "propose" in system.lower()
    assert "availability" in prompt.lower()
    assert "busy during 1 existing meeting" in prompt
