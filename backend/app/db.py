"""PostgreSQL persistence: ORM models, engines and scenario seeding.

Single-user application, so ``app_settings`` holds exactly one row (id = 1).

Two roles are used (see :mod:`app.config`): the *owner* role runs DDL and the
startup seeding, the *app* role serves every request. The app role's access to
the owner-created tables comes from server-side ``ALTER DEFAULT PRIVILEGES``
(see the bootstrap SQL in the deployment stack), so no GRANT is issued here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings
from .scenario_files import load_scenario_files

log = logging.getLogger(__name__)

SETTINGS_ROW_ID = 1


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

    wanikani_api_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ankiconnect_url: Mapped[str | None] = mapped_column(String, nullable=True)
    anki_deck_name: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_customized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    instructions: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# --- engines ---------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """The request-time engine (app role), created on first use."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().app_database_url, future=True)
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
    owner_engine = create_async_engine(settings.owner_database_url, future=True)
    try:
        async with owner_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate_schema(conn)
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            await ensure_settings_row(session)
            await seed_scenarios(session)
            await session.commit()
    finally:
        await owner_engine.dispose()


async def migrate_schema(conn: AsyncConnection) -> None:
    """Add columns that ``create_all`` cannot: it only creates missing *tables*.

    Keep the statements idempotent and append-only — an existing database
    carries real session history.
    """
    # No migrations yet; the hook exists so the first one has an obvious home.
    return None


async def ensure_settings_row(session: AsyncSession) -> None:
    await session.execute(
        insert(AppSettings)
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
        stmt = insert(Scenario).values(
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
