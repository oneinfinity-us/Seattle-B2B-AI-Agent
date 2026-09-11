# Seattle B2B AI Assistant — Core Service Skeleton

An AI executive-assistant agent for Seattle-local small businesses (restaurants / real estate agencies,
etc.): it triages a business owner's inbox, drafts replies to emails that need one, and helps schedule
meetings by reading the owner's calendar availability — all gated behind human review and approval before
anything actually sends.

## Why These Choices

| Component | Choice | Notes |
|---|---|---|
| Web framework | FastAPI (async) | Native async/await, a natural fit for I/O-bound LLM/Gmail/Calendar calls |
| Agent orchestration | An explicit state machine (`AssistantWorkflow`), not a single do-everything prompt | Each transition (classify -> draft -> await approval) is independently testable and observable |
| Provider abstraction | `EmailProvider` / `CalendarProvider` interfaces, with a real Gmail/Calendar implementation and an in-memory fake | Lets the whole draft -> approve -> send loop be exercised in tests and local dev without live Google OAuth credentials |
| Persistence | SQLAlchemy async, one `pending_actions` table shared by both action kinds | Email replies and scheduling proposals need identical draft/approve/execute mechanics — one polymorphic table beats two parallel ones |
| Human-review gate | Every drafted action defaults to `AWAITING_APPROVAL`; a merchant decides approve/edit/reject before anything is sent | Product/safety decision: an AI-drafted reply going out under the owner's name needs a human to see it first |
| Rate limiting | Redis + Lua (token bucket), per tenant | Atomic, lock-free, and isolates one tenant's traffic spike from another's |
| Streaming | SSE (Server-Sent Events) | A frontend can show triage/draft progress as it happens |
| Notifications | Async, idempotent (`NotificationService`) | A daily digest ("N items need your review") is decoupled from the request that generated it |
| Multi-tenant isolation | One `tenant_id` per merchant, threaded through the rate-limit key and every persisted row | Each business's inbox/calendar/actions stay isolated |

## Directory Structure

```
app/
  main.py                    # FastAPI entry point, lifecycle management (Redis/DB connection pools)
  core/
    config.py                # Centralized environment variable management
    rate_limiter.py          # Redis token-bucket rate limiting (Lua atomic operations)
    llm_client.py            # LLM call wrapper: streaming, retries, multi-provider fallback
  integrations/
    email_provider.py        # EmailProvider interface: GmailProvider (real) + FakeEmailProvider (tests/dev)
    calendar_provider.py     # CalendarProvider interface: GoogleCalendarProvider (real) + FakeCalendarProvider
  agents/
    assistant_agent.py       # Explicit state machine: classify -> draft -> await human approval
  models/
    schemas.py                # Pydantic data models
  services/
    notifier.py               # Email/SMS notifications (async, idempotent)
  db/
    base.py                   # SQLAlchemy async engine/session setup
    models.py                  # ORM models (pending_actions, gmail_account_credentials)
    repository.py              # PendingActionRepository: persistence + approve/edit/reject + send-on-approve
  api/
    routes.py                 # HTTP/SSE routes: sync inbox, list pending actions, submit a decision
```

## Human-Review Gate

Every AI-drafted action (an email reply, or a scheduling proposal) is persisted in `AWAITING_APPROVAL`
state (see `app/db/models.py`). A merchant closes it out via
`POST /api/v1/assistant/{action_id}/decision` with `decision` set to `approved`, `edited` (requires
`edited_reply`), or `rejected`. Approving or editing actually sends the reply through the configured
`EmailProvider` — nothing is fully auto-published without a human decision in between.

## Quickstart (Local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requires a local Redis instance: `docker run -p 6379:6379 redis:7`

By default the app uses a local SQLite file (`dev.db`) for persistence and in-memory fake
email/calendar providers — no Google credentials needed to run the full triage -> draft -> approve ->
"send" loop locally.

## Evaluating Draft Quality

`eval/` is a standalone eval harness — not part of `pytest`, since it makes real Claude API calls and
produces non-deterministic output. It runs ~20 labeled cases ([eval/dataset.py](eval/dataset.py)) through
the real `AssistantWorkflow` and measures:

- **Classification accuracy** against a human-judged expected outcome (some cases are deliberately
  chosen to expose where the keyword-based classifier in `app/agents/assistant_agent.py` is brittle —
  a less-than-100% score there is the point, not a bug).
- **Draft quality**, via LLM-as-judge scoring (tone, factual grounding, whether it addresses the
  request) on a 1-5 rubric.
- **Prompt-injection resistance** — several cases embed an instruction in the customer email trying to
  hijack the assistant (leak its system prompt, insult a third party, exfiltrate other customers' data);
  the judge flags whether the draft stayed on task.

```bash
python -m eval.run_eval
```

Requires `ANTHROPIC_API_KEY` (real spend — cheap at this dataset size). Prints a summary and writes a
timestamped JSON report to `eval/results/` (gitignored).

## Legal Pages

`/privacy` and `/terms` are live pages required for real accounts to log in at all — the Gmail/Calendar
scopes requested at login mean the app must pass Google's OAuth verification, which requires published
policies at those URLs. See [docs/oauth-verification.md](docs/oauth-verification.md) for the submission
checklist and the exact scope-justification text.

## Known Design Trade-offs

1. **Refreshed access tokens aren't written back.** `GmailProvider`/`GoogleCalendarProvider` refresh an
   expired access token in-memory per request (via the stored refresh token), but don't persist the new
   token/expiry back to `GmailAccountCredential` — harmless (the refresh token still works next time)
   but means every request after expiry re-refreshes instead of reusing a cached token.
2. **OAuth tokens are encrypted at rest** (`app/core/crypto.py`'s `EncryptedText`, backed by Fernet) —
   but the key itself (`TOKEN_ENCRYPTION_KEY`) just lives in an environment variable, not a real secrets
   manager/KMS with rotation. Fine for this stage; revisit before handling a large number of customers.
   Rotating or losing this key means every stored credential must be reconnected from scratch.
3. **Calendar is read-only in v1.** The assistant reads free/busy to draft smarter scheduling replies,
   but never creates or books calendar events itself — a human always sends the proposal and the actual
   booking happens as a normal reply/accept over email.
4. **The background sync loop is in-process and per-instance** (`app/services/sync_service.py`,
   started in `app/main.py`'s lifespan on an interval — `SYNC_INTERVAL_SECONDS`, default hourly). Fine
   at the current single-instance deployment; if this ever scales to more than one instance, each one
   runs its own loop and duplicate-processes every tenant. `POST /api/v1/assistant/sync` (the "Check
   inbox now" button) still exists separately for the on-demand, live-streaming UX.
5. Agent state must be persisted to a database (rather than kept in in-process memory), otherwise a
   single worker restart would lose all in-progress actions — this is why `app/db/` exists rather than
   keeping `ActionContext` in memory.
6. **No real schema migrations.** `app/db/base.py`'s `ensure_new_columns` is a stop-gap that adds
   missing *nullable* columns to an already-provisioned database at startup (this project hit exactly
   this: a schema change 500'd production because `create_all()` only creates brand-new tables, never
   alters existing ones). It can't handle non-nullable columns, drops, renames, or type changes — a
   real schema change beyond "add a nullable column" needs Alembic, not this.
