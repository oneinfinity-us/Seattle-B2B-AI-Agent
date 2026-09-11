from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings
from app.db import Base


def _alembic_config() -> Config:
    repo_root = Path(__file__).resolve().parent.parent
    return Config(str(repo_root / "alembic.ini"))


def test_initial_migration_upgrade_matches_current_models_and_downgrade_is_clean(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()

    try:
        cfg = _alembic_config()
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            for table in Base.metadata.sorted_tables:
                assert table.name in tables
                actual_columns = {c["name"] for c in inspector.get_columns(table.name)}
                expected_columns = {c.name for c in table.columns}
                assert actual_columns == expected_columns, f"{table.name} columns drifted from the migration"
        finally:
            engine.dispose()

        command.downgrade(cfg, "base")

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            remaining_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
            assert remaining_tables == set()
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
