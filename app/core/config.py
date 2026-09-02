"""
集中管理环境变量。用 pydantic-settings 而不是 os.environ 散落各处，
这样面试官问"你怎么管理配置/密钥"时可以直接展示这个类。
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM providers（主 + 备用，用于 fallback）
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    primary_model: str = "claude-sonnet-4-6"
    fallback_model: str = "gpt-4o-mini"

    # Redis：既做缓存，也做限流令牌桶，也做 arq 任务队列
    redis_url: str = "redis://localhost:6379/0"

    # 限流：每个商户（tenant）每分钟允许多少次 LLM 调用
    rate_limit_capacity: int = 30
    rate_limit_refill_per_sec: float = 0.5

    # 语义缓存相似度阈值，超过则认为是"同一条评论的变体"，复用缓存结果
    semantic_cache_similarity_threshold: float = 0.92

    # 向量数据库
    qdrant_url: str = "http://localhost:6333"

    # 通知渠道
    sendgrid_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
