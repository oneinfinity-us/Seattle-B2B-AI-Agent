from __future__ import annotations

from app.db.base import _normalize_database_url


def test_normalizes_bare_postgres_scheme():
    assert _normalize_database_url("postgres://user:pw@host/db").startswith("postgresql+asyncpg://user:pw@host/db")


def test_normalizes_bare_postgresql_scheme():
    assert _normalize_database_url("postgresql://user:pw@host/db").startswith(
        "postgresql+asyncpg://user:pw@host/db"
    )


def test_leaves_already_normalized_url_untouched():
    url = "postgresql+asyncpg://user:pw@host/db"
    assert _normalize_database_url(url) == url


def test_leaves_sqlite_url_untouched():
    url = "sqlite+aiosqlite:///./dev.db"
    assert _normalize_database_url(url) == url
