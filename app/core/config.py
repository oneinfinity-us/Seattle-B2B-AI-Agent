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

    # Rate limiting: how many LLM calls each merchant (tenant) is allowed per minute
    rate_limit_capacity: int = 30
    rate_limit_refill_per_sec: float = 0.5

    # Semantic cache similarity threshold; above this, treat it as a "variant of the same review" and reuse the cached result
    semantic_cache_similarity_threshold: float = 0.92

    # Vector database
    qdrant_url: str = "http://localhost:6333"

    # Notification channels
    sendgrid_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
