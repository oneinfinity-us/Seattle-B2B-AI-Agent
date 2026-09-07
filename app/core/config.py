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

    # Google OAuth client, used to mint/refresh Gmail + Calendar access tokens for a tenant's
    # GmailAccountCredential. Phase A provisions the credential itself out-of-band (see README); this
    # client id/secret is still needed to refresh an expired access token.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # Notification channels
    sendgrid_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
