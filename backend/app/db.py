"""Persistence: ORM models, engines and scenario seeding.

Single-user application, so ``app_settings`` holds exactly one row (id = 1).

Postgres is the deployment target. Two roles are used (see :mod:`app.config`):
the *owner* role runs DDL and the startup seeding, the *app* role serves every
request. The app role's access to the owner-created tables comes from
server-side ``ALTER DEFAULT PRIVILEGES`` (see the bootstrap SQL in the
deployment stack), so no GRANT is issued here.

A ``sqlite://`` DB_URL runs the same schema out of a local file instead, for a
machine that has no Postgres to point at. Everything that differs between the
two backends is collected here rather than sprinkled through the request paths:
the column types (:data:`JSONColumn`, :class:`UtcDateTime`), the upsert
(:func:`_upsert`), the schema migration and the connection setup. Nothing above
this module needs to know which one is in use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    event,
    false,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from sqlalchemy.engine import URL, make_url

from .config import get_settings
from .scenario_files import load_scenario_files

log = logging.getLogger(__name__)

SETTINGS_ROW_ID = 1

# Errors that will never resolve by waiting: the roles, the password or the
# database itself are wrong, so retrying only delays a clear failure.
FATAL_SQLSTATES = {
    # Postgres deliberately reports a missing role and a wrong password with
    # the same code, so that an attacker cannot enumerate users. The message
    # has to name both causes -- claiming only "wrong password" sends someone
    # looking for a role that was never created.
    "28P01": (
        "authentication as {user} failed. Either the role does not exist, or its "
        "password differs from DB_OWNER_PASSWORD -- Postgres reports both the "
        "same way. Check with dbeaver/verify.sql; if the roles are missing, run "
        "dbeaver/create_users_and_db.sql and grant_privileges.sql"
    ),
    "28000": (
        "role {user} does not exist. Run dbeaver/create_users_and_db.sql "
        "followed by dbeaver/grant_privileges.sql"
    ),
    "3D000": (
        "database {database} does not exist. Run dbeaver/create_users_and_db.sql "
        "followed by dbeaver/grant_privileges.sql"
    ),
}


class DatabaseUnavailable(RuntimeError):
    """The database could not be used, with a reason worth reading."""


def _fatal_reason(exc: BaseException) -> str | None:
    """Return a human explanation when the error cannot be fixed by waiting."""
    seen: list[BaseException | None] = [exc, getattr(exc, "orig", None), exc.__cause__]
    for candidate in seen:
        code = getattr(candidate, "sqlstate", None)
        if code in FATAL_SQLSTATES:
            settings = get_settings()
            return FATAL_SQLSTATES[code].format(
                user=settings.db_owner_user, database=make_url(settings.db_url).database
            )
    return None


# --- portable column types -------------------------------------------------

# JSONB where it exists, JSON where it does not. Declared this way round so the
# Postgres DDL is byte-for-byte what it already was -- an existing deployment
# keeps its JSONB columns.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC, on both backends.

    SQLite has no timestamp type: ``DateTime(timezone=True)`` writes an ISO
    string without an offset and reads a *naive* datetime back. FastAPI then
    serialises it without a zone, and the browser reads it as local time -- a
    history that is silently off by the UTC offset. Attaching UTC on the way
    out fixes that; ``CURRENT_TIMESTAMP`` is UTC in SQLite, so the assumption
    holds for server-side defaults too.

    On Postgres the value already arrives aware and only gets normalised.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    """User-editable configuration, exactly one row.

    Every column is nullable: a NULL means "not configured here", and the
    corresponding environment variable is used instead. That keeps a fresh
    deployment working from its .env alone while letting the Settings screen
    override anything without a redeploy.
    """

    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint("id = 1", name="app_settings_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    openai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    realtime_model: Mapped[str | None] = mapped_column(String, nullable=True)
    analysis_model: Mapped[str | None] = mapped_column(String, nullable=True)
    # The scenario editor's assistant is deliberately separate: it writes prose
    # rather than driving a conversation, so it is worth paying for a stronger
    # model there than in the live session.
    scenario_assistant_model: Mapped[str | None] = mapped_column(String, nullable=True)
    transcription_model: Mapped[str | None] = mapped_column(String, nullable=True)
    tts_model: Mapped[str | None] = mapped_column(String, nullable=True)

    realtime_voice: Mapped[str | None] = mapped_column(String, nullable=True)
    realtime_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    realtime_vad_eagerness: Mapped[str | None] = mapped_column(String, nullable=True)

    wanikani_api_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ankiconnect_url: Mapped[str | None] = mapped_column(String, nullable=True)
    anki_deck_name: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class Scenario(Base):
    """A role-play setting the learner can pick.

    ``prompt`` is English (it goes to the model), ``title`` is German (it goes
    to the UI). Scenarios shipped as Markdown files are re-seeded on every boot
    unless ``is_customized`` is set — editing a built-in scenario in the UI
    marks it, so a redeploy never overwrites the user's own wording.
    """

    __tablename__ = "scenarios"
    __table_args__ = (Index("idx_scenarios_sort", "sort_order", "title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # `false()`, not the string "false": SQLite would store that literally as
    # text and read it back as True, so every seeded scenario would look
    # customised and never be refreshed from its file again.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_customized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class Session(Base):
    """One finished conversation, kept for the history screen.

    The scenario is denormalised into ``scenario_title`` / ``scenario_prompt``
    on purpose: a session records what actually happened, so editing or
    deleting the scenario afterwards must not rewrite history.
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("idx_sessions_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    scenario_title: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    scenario_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    jlpt_level: Mapped[str] = mapped_column(String, nullable=False, server_default="N5")

    model: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    voice: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    speed: Mapped[float] = mapped_column(Float, nullable=False, server_default="1")
    vad_eagerness: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    usage: Mapped[dict[str, Any]] = mapped_column(
        JSONColumn, nullable=False, server_default="{}"
    )
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)


# --- engines ---------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _new_engine(url: URL) -> AsyncEngine:
    """Create an engine, with SQLite's per-connection setup attached."""
    engine = create_async_engine(url, future=True)
    if url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def _configure_sqlite_connection(connection: Any, _record: Any) -> None:
    """The three PRAGMAs a SQLite file needs to behave like the Postgres one.

    * ``foreign_keys`` is off by default, and without it ``ON DELETE SET NULL``
      on ``sessions.scenario_id`` is silently ignored -- deleting a scenario
      would leave sessions pointing at a row that is gone.
    * ``journal_mode=WAL`` lets a read run while a write is in flight, which
      the default rollback journal does not.
    * ``busy_timeout`` turns the remaining overlaps into a short wait instead
      of an immediate "database is locked".
    """
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _upsert(table: type[Base] | Table) -> Any:
    """``INSERT .. ON CONFLICT``, from whichever dialect is in use.

    Both dialects offer it with the same arguments, but the constructs come
    from different modules and neither accepts the other's.
    """
    if get_settings().uses_sqlite:
        return sqlite_insert(table)
    return postgresql_insert(table)


def get_engine() -> AsyncEngine:
    """The request-time engine (app role), created on first use."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _new_engine(get_settings().app_database_url)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def reset_engines() -> None:
    """Drop the cached engine so the next use re-reads the configuration.

    Production never needs this; the tests do, because they point the process
    at a throwaway database between cases.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one app-role session per request."""
    async with get_sessionmaker()() as session:
        yield session


# --- schema + seeding ------------------------------------------------------


async def init_db() -> None:
    """Create the schema and seed the built-in scenarios (run on startup).

    DDL requires the owner role, so this opens a short-lived owner connection.
    Seeding rides along on it: it is maintenance, not request work.
    """
    settings = get_settings()
    _prepare_sqlite_directory(settings.sqlite_path)
    owner_engine = _new_engine(settings.owner_database_url)
    try:
        await _wait_for_database(owner_engine)
        async with owner_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate_schema(conn)
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            await ensure_settings_row(session)
            await seed_scenarios(session)
            await session.commit()
    finally:
        await owner_engine.dispose()


def _prepare_sqlite_directory(path: Any) -> None:
    """Create the directory the SQLite file lives in, if it is missing.

    SQLite will create the *file* but not the folder above it, and reports the
    missing folder as "unable to open database file" -- which reads like a
    permission problem and sends you looking in the wrong place.
    """
    if path is None or path.parent == path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)


async def _wait_for_database(engine: AsyncEngine) -> None:
    """Block until the database accepts a connection, or fail with a reason.

    Waiting is right for a database that is merely not up yet, and wrong for
    one that will never let us in: a bad password or a missing role stays bad,
    so those raise immediately with an explanation instead of a stack trace
    repeated once per restart.
    """
    settings = get_settings()

    if settings.uses_sqlite:
        # Nothing to wait for: a local file is either openable now or it never
        # will be. Retrying a read-only directory for a minute helps nobody.
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - re-raised with the path named
            location = settings.sqlite_path or ":memory:"
            reason = f"the SQLite database at {location} could not be opened: {exc}"
            log.error("Cannot use the database: %s.", reason)
            raise DatabaseUnavailable(reason) from None
        return

    attempts = max(1, settings.db_connect_attempts)

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if attempt > 1:
                log.info("Database reachable after %d attempts", attempt)
            return
        except Exception as exc:  # noqa: BLE001 - re-raised below unless retryable
            reason = _fatal_reason(exc)
            if reason is not None:
                log.error("Cannot use the database: %s.", reason)
                # `from None`: the driver traceback adds nothing to a message
                # that already says exactly what to change.
                raise DatabaseUnavailable(reason) from None

            if attempt == attempts:
                log.error(
                    "Database still unreachable after %d attempts (%.0fs)",
                    attempts,
                    attempts * settings.db_connect_delay_seconds,
                )
                raise

            log.warning(
                "Database not reachable yet (attempt %d/%d): %s", attempt, attempts, exc
            )
            await asyncio.sleep(settings.db_connect_delay_seconds)


# Columns added after their table first shipped. Append-only: an existing
# database carries real session history, so a line here is never edited or
# removed, only added to.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("app_settings", "realtime_vad_eagerness", "VARCHAR"),
    ("sessions", "vad_eagerness", "VARCHAR NOT NULL DEFAULT ''"),
)


async def migrate_schema(conn: AsyncConnection) -> None:
    """Add columns that ``create_all`` cannot: it only creates missing *tables*.

    Idempotence comes from asking which columns exist rather than from
    ``ADD COLUMN IF NOT EXISTS``, which SQLite does not have. Reflecting first
    works the same on both backends and reads as what it is.
    """
    existing = await conn.run_sync(_existing_columns, {table for table, _, _ in ADDED_COLUMNS})

    for table, column, definition in ADDED_COLUMNS:
        if column in existing[table]:
            continue
        log.info("Adding missing column %s.%s", table, column)
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _existing_columns(sync_conn: Any, tables: set[str]) -> dict[str, set[str]]:
    """Which columns each table currently has (runs on a sync connection)."""
    inspector = inspect(sync_conn)
    return {
        table: {column["name"] for column in inspector.get_columns(table)} for table in tables
    }


async def ensure_settings_row(session: AsyncSession) -> None:
    await session.execute(
        _upsert(AppSettings)
        .values(id=SETTINGS_ROW_ID)
        .on_conflict_do_nothing(index_elements=[AppSettings.id])
    )


async def seed_scenarios(session: AsyncSession) -> None:
    """Insert the Markdown-defined scenarios, refreshing the untouched ones.

    A scenario the user has edited (``is_customized``) keeps its wording: the
    upsert deliberately skips those rows rather than restoring the file text.
    """
    files = load_scenario_files()
    if not files:
        log.warning("No scenario files found; starting without built-in scenarios")
        return

    for index, entry in enumerate(files):
        stmt = _upsert(Scenario).values(
            slug=entry.slug,
            title=entry.title,
            summary=entry.summary,
            prompt=entry.prompt,
            is_builtin=True,
            sort_order=index,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Scenario.slug],
                set_={
                    "title": stmt.excluded.title,
                    "summary": stmt.excluded.summary,
                    "prompt": stmt.excluded.prompt,
                    "sort_order": stmt.excluded.sort_order,
                    "updated_at": func.now(),
                },
                where=Scenario.is_customized.is_(False),
            )
        )

    log.info("Seeded %d built-in scenario(s)", len(files))


async def load_settings(session: AsyncSession) -> AppSettings | None:
    """Read the single settings row, if it exists yet."""
    return await session.scalar(
        select(AppSettings).where(AppSettings.id == SETTINGS_ROW_ID)
    )
