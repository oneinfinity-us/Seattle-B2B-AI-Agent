from __future__ import annotations

import structlog
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = structlog.get_logger()


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


async def ensure_new_columns(engine: AsyncEngine, base: type[DeclarativeBase]) -> None:
    """
    MVP stop-gap, not a substitute for real migrations (Alembic): `Base.metadata.create_all()` only
    creates TABLES that don't exist yet — it never adds a COLUMN to a table that's already there. Every
    schema change since the first deploy (drafted_at, reject_reason, ...) silently did nothing on an
    already-provisioned database until this runs, so a query selecting the new column would 500 with
    "column does not exist". This only ever adds nullable columns (safe on a table with existing rows
    without a DEFAULT); non-nullable additions are skipped with a warning and need a real migration.
    """

    def _find_missing(sync_conn) -> list[tuple[str, str, object]]:
        inspector = inspect(sync_conn)
        missing: list[tuple[str, str, object]] = []
        for table in base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # a brand-new table: create_all already created it with every current column
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    logger.warning("skipping_non_nullable_column", table=table.name, column=column.name)
                    continue
                missing.append((table.name, column.name, column.type))
        return missing

    async with engine.connect() as conn:
        missing_columns = await conn.run_sync(_find_missing)

    for table_name, column_name, column_type in missing_columns:
        ddl_type = column_type.compile(dialect=engine.dialect)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {ddl_type}'))
            logger.info("added_missing_column", table=table_name, column=column_name)
        except Exception:
            logger.warning("failed_to_add_missing_column", table=table_name, column=column_name, exc_info=True)
