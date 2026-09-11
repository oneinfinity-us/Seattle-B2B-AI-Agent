from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _normalize_database_url(database_url: str) -> str:
    """Managed Postgres providers (Render, Railway, RDS, ...) hand out plain "postgres://" or
    "postgresql://" connection strings; our async engine needs the asyncpg driver spelled out."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix) and "+asyncpg" not in database_url:
            return "postgresql+asyncpg://" + database_url[len(prefix) :]
    return database_url


def create_engine_and_sessionmaker(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_normalize_database_url(database_url), future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessionmaker


def sync_database_url(database_url: str) -> str:
    """Alembic (alembic/env.py) runs migrations with a plain sync engine, not the app's async one --
    asyncpg has no sync interface, so this swaps it for psycopg2 (SQLite's stdlib driver needs no swap
    at all). The opposite direction of _normalize_database_url."""
    for async_prefix, sync_prefix in (
        ("postgresql+asyncpg://", "postgresql+psycopg2://"),
        ("postgres://", "postgresql+psycopg2://"),
        ("sqlite+aiosqlite://", "sqlite://"),
    ):
        if database_url.startswith(async_prefix):
            return sync_prefix + database_url[len(async_prefix) :]
    return database_url
