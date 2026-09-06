from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

# Imported for its side effect: registers ReviewWorkflowRecord on Base.metadata.
from app.db.models import ReviewWorkflowRecord  # noqa: F401


@pytest_asyncio.fixture
async def db_sessionmaker():
    # StaticPool keeps a single connection alive for the whole engine, which is required for an
    # in-memory SQLite database — otherwise each checkout would get its own empty :memory: database.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield sessionmaker

    await engine.dispose()
