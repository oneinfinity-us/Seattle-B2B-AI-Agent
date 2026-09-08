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

## Deploying (MVP)

For a pilot with a handful of real merchants, skip Kubernetes/EKS — it solves scaling problems this
stage doesn't have yet, and the control plane alone runs ~$70+/month before any real usage. A
PaaS (Render, Railway, or similar) gets a real HTTPS URL + managed Postgres + managed Redis running in
minutes:

1. Push this repo (the included `Dockerfile` builds it as-is).
2. Create three resources on the platform: a **web service** built from `Dockerfile`, a **Postgres**
   database, and a **Redis** instance.
3. Set these environment variables on the web service (get Postgres/Redis connection strings from the
   platform — `postgres://`/`postgresql://` URLs are normalized to the asyncpg driver automatically, see
   `app/db/base.py`):
   - `DATABASE_URL` — the Postgres connection string
   - `REDIS_URL` — the Redis connection string
   - `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` — from Google Cloud Console
   - `GOOGLE_OAUTH_REDIRECT_URI` — `https://<your-app-domain>/api/v1/auth/google/callback`
   - `ANTHROPIC_API_KEY`
   - `SESSION_COOKIE_SECURE=true` — required once served over HTTPS (anything not `localhost`)
4. In Google Cloud Console's OAuth client, add the same `https://<your-app-domain>/api/v1/auth/google/callback`
   to **Authorized redirect URIs**.
5. Healthcheck path for the platform: `GET /healthz`.

A from-scratch Terraform + Kubernetes/EKS deployment is a separate, valid exercise (e.g. for
demonstrating that experience) — see the project history for that plan if picking it back up; it's
deliberately not what this MVP path uses.

## Known Design Trade-offs

1. **Refreshed access tokens aren't written back.** `GmailProvider`/`GoogleCalendarProvider` refresh an
   expired access token in-memory per request (via the stored refresh token), but don't persist the new
   token/expiry back to `GmailAccountCredential` — harmless (the refresh token still works next time)
   but means every request after expiry re-refreshes instead of reusing a cached token.
2. **OAuth tokens are stored in plaintext** in `gmail_account_credentials`. This is fine for local
   development but must not go to production as-is — needs field-level encryption or a secrets manager
   before onboarding real customers.
3. **Calendar is read-only in v1.** The assistant reads free/busy to draft smarter scheduling replies,
   but never creates or books calendar events itself — a human always sends the proposal and the actual
   booking happens as a normal reply/accept over email.
4. **Sync is manually triggered**, not scheduled. `POST /api/v1/assistant/sync` stands in for "checks
   email daily" — wiring a cron/scheduler to call it on an interval is a deployment concern, not
   something this service does for itself yet.
5. Agent state must be persisted to a database (rather than kept in in-process memory), otherwise a
   single worker restart would lose all in-progress actions — this is why `app/db/` exists rather than
   keeping `ActionContext` in memory.
