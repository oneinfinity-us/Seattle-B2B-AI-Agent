from __future__ import annotations

import os

# Set before any app module import that might construct Settings — EncryptedText (used by
# GmailAccountCredential) needs a valid key to encrypt/decrypt in tests. Not a production secret.
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "euetz15u41knY-OqdlCsLI0yBhGfjZLkAojsdSUAI8E=")

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

# Imported for its side effect: registers these models on Base.metadata.
from app.db.models import GmailAccountCredential, PendingActionRecord  # noqa: F401


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
