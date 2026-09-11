"""
Centralized management of environment variables. Uses pydantic-settings instead of os.environ scattered
everywhere, so when an interviewer asks "how do you manage configuration/secrets" you can show this
class directly.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM providers (primary + fallback, used for failover)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    primary_model: str = "claude-sonnet-4-6"
    fallback_model: str = "gpt-4o-mini"

    # Redis: used for caching, the rate-limit token bucket, and the arq task queue
    redis_url: str = "redis://localhost:6379/0"

    # Workflow persistence (tenants/pending actions/approval decisions). Defaults to a local SQLite
    # file so `uvicorn --reload` works with no extra infra; production should point this at Postgres,
    # e.g. "postgresql+asyncpg://user:pass@host/db".
    database_url: str = "sqlite+aiosqlite:///./dev.db"

    # Rate limiting: how many LLM calls each merchant (tenant) is allowed per minute
    rate_limit_capacity: int = 30
    rate_limit_refill_per_sec: float = 0.5

    # How often the background loop (app/services/sync_service.py) checks every onboarded tenant's
    # inbox, in seconds. Runs in-process -- fine at single-instance scale, see README.
    sync_interval_seconds: int = 60 * 60

    # Google OAuth: this is now also how a merchant logs in (see app/api/auth_routes.py) — one
    # "Continue with Google" grant both authenticates them and captures the refresh token their
    # GmailAccountCredential needs for Gmail/Calendar access.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    # Server-side session (Redis-backed) issued after a successful Google login. tenant_id is the
    # connected Google account's email address. session_cookie_secure must be true once the app is
    # served over HTTPS (any real deployment) — false only for http://localhost dev.
    session_cookie_name: str = "session"
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    session_cookie_secure: bool = False

    # Notification channels. SendGrid requires notification_from_email to be a verified sender (Single
    # Sender Verification or a fully authenticated domain) in that SendGrid account, or sends 403.
    sendgrid_api_key: str = ""
    notification_from_email: str = "notifications@example.com"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # Encrypts GmailAccountCredential.refresh_token/access_token at rest (see app/core/crypto.py).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Losing this key makes every stored credential unrecoverable — every connected merchant would need
    # to reconnect their Google account.
    token_encryption_key: str = ""

    # Error tracking (app/main.py). Unhandled request exceptions are captured automatically once this
    # is set; background-task failures (the sync loop, notification delivery) call
    # sentry_sdk.capture_exception() explicitly at their existing try/except sites — see those call
    # sites for why. Safe to leave unset: every capture call is a no-op until sentry_sdk.init() runs.
    sentry_dsn: str = ""
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
