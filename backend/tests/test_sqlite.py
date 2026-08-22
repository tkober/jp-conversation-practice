"""The SQLite backend, exercised regardless of which database the suite runs on.

``TEST_DB=sqlite`` points the *whole* suite at SQLite, which is the real
coverage. This module is the part that must hold even on a Postgres run,
because it covers the places where the two backends genuinely differ: the
column types, the dialect's upsert, the reflection-based migration and the
fact that SQLite has no roles to split.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db
from app.config import get_settings


@pytest.fixture
async def sqlite_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker]:
    """A freshly built SQLite database, with an app-side sessionmaker on it.

    The engine is created here rather than through ``db.get_engine()`` so the
    module-level one keeps pointing wherever the suite's own fixture put it.
    """
    settings = get_settings()
    # Deliberately below a directory that does not exist yet.
    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path / 'data' / 'jp.db'}")

    await db.init_db()

    engine = db._new_engine(settings.app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def test_both_roles_resolve_to_the_same_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQLite has no roles -- the owner/app split is a Postgres privilege."""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_url", "sqlite:///./data/jp.db")

    assert settings.uses_sqlite
    assert settings.app_database_url == settings.owner_database_url
    assert settings.app_database_url.drivername == "sqlite+aiosqlite"
    assert settings.sqlite_path == Path("./data/jp.db")


def test_a_postgres_url_still_carries_the_two_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "db_url", "postgresql://host:5432/jp")

    assert not settings.uses_sqlite
    assert settings.sqlite_path is None
    assert settings.app_database_url.username == settings.db_user
    assert settings.owner_database_url.username == settings.db_owner_user


async def test_init_db_creates_the_file_and_the_directory_above_it(
    sqlite_db: async_sessionmaker, tmp_path: Path
) -> None:
    """SQLite creates the file but not the folder, and blames the file for it."""
    assert (tmp_path / "data" / "jp.db").is_file()

    async with sqlite_db() as session:
        assert await session.scalar(select(db.Scenario.slug)) is not None


async def test_seeded_scenarios_are_not_marked_as_customised(
    sqlite_db: async_sessionmaker,
) -> None:
    """The boolean defaults have to be real booleans.

    ``server_default="false"`` renders as the *string* 'false' in SQLite, which
    reads back as True -- every built-in scenario would look edited by hand and
    would never be refreshed from its file again.
    """
    async with sqlite_db() as session:
        rows = (await session.scalars(select(db.Scenario))).all()

    assert rows, "the built-in scenarios should have been seeded"
    assert all(row.is_builtin is True for row in rows)
    assert all(row.is_customized is False for row in rows)


async def test_seeding_refreshes_built_ins_but_leaves_edits_alone(
    sqlite_db: async_sessionmaker,
) -> None:
    """The upsert is dialect-specific; this is it running on SQLite."""
    async with sqlite_db() as session:
        edited, untouched = (await session.scalars(select(db.Scenario).limit(2))).all()
        original_prompt = untouched.prompt

        edited.prompt = "Von Hand geändert"
        edited.is_customized = True
        untouched.prompt = "Aus dem Ruder gelaufen"
        await session.commit()

        await db.seed_scenarios(session)
        await session.commit()

        await session.refresh(edited)
        await session.refresh(untouched)

        assert edited.prompt == "Von Hand geändert"
        assert untouched.prompt == original_prompt


async def test_a_session_row_round_trips_its_json_and_its_timestamp(
    sqlite_db: async_sessionmaker,
) -> None:
    """SQLite has no JSONB and no timestamp type; both have to survive anyway."""
    async with sqlite_db() as session:
        row = db.Session(
            scenario_title="Kombini",
            usage={"cost_usd": 0.12, "input": {"audio_tokens": 7}},
            transcript=[{"role": "user", "text": "これください"}],
            analysis=None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        row_id = row.id

    async with sqlite_db() as session:
        stored = await session.get(db.Session, row_id)

    assert stored is not None
    assert stored.usage["input"]["audio_tokens"] == 7
    assert stored.transcript[0]["text"] == "これください"
    # Naive here would mean the browser reads the history in the wrong zone.
    assert stored.started_at.tzinfo is not None
    assert abs((stored.started_at - datetime.now(timezone.utc)).total_seconds()) < 120


async def test_migrate_schema_adds_a_missing_column_and_repeats_cleanly(
    sqlite_db: async_sessionmaker,
) -> None:
    """SQLite has no ADD COLUMN IF NOT EXISTS, so the check is a reflection.

    Every row of ADDED_COLUMNS is dropped and restored, so a row added later
    is covered here without anyone remembering to extend this test -- including
    whether its Postgres type spelling is one SQLite will accept.
    """
    settings = get_settings()
    engine = db._new_engine(settings.owner_database_url)
    tables = {table for table, _, _ in db.ADDED_COLUMNS}
    try:
        async with engine.begin() as conn:
            for table, column, _ in db.ADDED_COLUMNS:
                await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            await db.migrate_schema(conn)
            # Running it twice must not raise: it is a boot-time step.
            await db.migrate_schema(conn)

        async with engine.connect() as conn:
            columns = await conn.run_sync(db._existing_columns, tables)
    finally:
        await engine.dispose()

    for table, column, _ in db.ADDED_COLUMNS:
        assert column in columns[table], f"{table}.{column} was not restored"
