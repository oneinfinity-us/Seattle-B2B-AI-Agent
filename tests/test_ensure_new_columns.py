from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from app.db.base import ensure_new_columns


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "widgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    note: Mapped[str | None] = mapped_column(nullable=True)  # added to the model after the table existed
    required_new: Mapped[str] = mapped_column(nullable=False)  # non-nullable -> must be skipped, not crash


async def test_adds_missing_nullable_columns_but_skips_non_nullable_ones():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    try:
        # Simulate a table created before `note`/`required_new` existed on the model.
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR)"))

        await ensure_new_columns(engine, _Base)

        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("widgets")})

        assert "note" in columns
        assert "required_new" not in columns
    finally:
        await engine.dispose()


async def test_is_a_no_op_when_the_table_already_has_every_column():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)

        await ensure_new_columns(engine, _Base)  # should not raise, nothing to add

        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("widgets")})
        assert columns == {"id", "name", "note", "required_new"}
    finally:
        await engine.dispose()
