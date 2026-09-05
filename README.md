# Yelp Review AI Agent — Core Service Skeleton

A Yelp review auto-analysis + auto reply-draft + notification agent for Seattle-local B2B merchants (restaurants / real estate agencies, etc.).

## Why These Choices

| Component | Choice | Talking points for interviews |
|---|---|---|
| Web framework | FastAPI (async) | Native async/await, a natural fit for I/O-bound LLM calls and concurrent notifications |
| Agent orchestration | A LangGraph-style explicit state machine (this skeleton demonstrates the principle with a lightweight homegrown version; in production, go straight to LangGraph) | Shows you understand a "graph state machine" rather than writing the agent as a pile of if-else |
| Cache / rate limiting | Redis + Lua scripts (token bucket) | Atomic, lock-free, can hit tens of thousands of QPS on a single instance — you can clearly explain why an in-app memory counter isn't used |
| Semantic cache | Embedding + cosine-similarity deduplication | Avoids repeatedly calling the LLM for "duplicate/near-duplicate reviews," directly saving on token cost |
| Streaming response | SSE (Server-Sent Events) | The frontend can display the draft as it's generated; lighter-weight than WebSocket, and sufficient for a one-way scenario |
| Notifications | Async task (arq worker, backed by a Redis Queue) | Shows you know that "calling the LLM" and "sending a notification" must be decoupled, not crammed into the same request |
| Vector search | Qdrant (pgvector is a fine swap) | RAG over a merchant's historical reviews / brand-voice knowledge base, so replies match that merchant's tone |
| Multi-tenant isolation | One tenant_id per merchant, threaded through the rate-limit key / cache key / vector collection | Pre-answers the interviewer's favorite question, "how do you do multi-tenant isolation" |

## Directory Structure

```
app/
  main.py                 # FastAPI entry point, lifecycle management (Redis/HTTP client connection pools)
  core/
    config.py             # Centralized environment variable management
    rate_limiter.py        # Redis token-bucket rate limiting (Lua atomic operations)
    cache.py               # Semantic cache (embedding similarity)
    llm_client.py           # LLM call wrapper: streaming, retries, multi-provider fallback
  agents/
    review_agent.py         # Explicit state machine: fetch -> classify -> generate -> human approval -> notify
  models/
    schemas.py              # Pydantic data models
  services/
    notifier.py             # Email/SMS notifications (async, idempotent)
  api/
    routes.py               # HTTP/SSE routes
```

## Quickstart (Local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Requires a local Redis instance: `docker run -p 6379:6379 redis:7`

## Known Design Trade-offs (bring these up proactively in interviews — see part 5 of the chat transcript)

1. The official Yelp Fusion API does not provide bulk fetching of full review text (a ToS restriction); production would need to go through the Yelp Partner API / a third-party review aggregation API, or fall back to "manual merchant sync" first.
2. All "auto-reply" defaults to a human-review gate — nothing is fully auto-published. This is a product/safety decision, not a technical limitation.
3. Agent state must be persisted to a database (rather than kept in in-process memory), otherwise a single worker restart would lose all in-progress workflows.
