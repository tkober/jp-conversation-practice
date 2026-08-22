"""Test fixtures: a throwaway Postgres with the deployment's two roles.

The owner/app role split is reproduced here rather than run as a single
superuser, so a mistake like issuing DDL from a request path fails in the
tests instead of at deploy time.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app import db
from app.config import get_settings

DB_NAME = "jp_conversation_test"
OWNER_USER = "jp_conversation_owner"
APP_USER = "jp_conversation_app"
ROLE_PASSWORD = "test"

# Mirrors the bootstrap SQL that runs once against postgres-core.
BOOTSTRAP = f"""
CREATE ROLE {OWNER_USER} WITH LOGIN PASSWORD '{ROLE_PASSWORD}';
CREATE ROLE {APP_USER} WITH LOGIN PASSWORD '{ROLE_PASSWORD}';
CREATE DATABASE {DB_NAME} OWNER {OWNER_USER};
GRANT CONNECT ON DATABASE {DB_NAME} TO {APP_USER};
"""

GRANTS = f"""
ALTER SCHEMA public OWNER TO {OWNER_USER};
GRANT USAGE ON SCHEMA public TO {APP_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_USER} IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_USER} IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {APP_USER};
"""


async def _run_statements(url: str, script: str) -> None:
    """Execute a bootstrap script statement by statement (no transaction).

    CREATE DATABASE cannot run inside a transaction block, hence AUTOCOMMIT.
    """
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for statement in filter(None, (s.strip() for s in script.split(";"))):
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:17", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session", autouse=True)
def database(postgres: PostgresContainer) -> Iterator[None]:
    """Create the roles and database, point the app at them, build the schema."""
    admin_url = postgres.get_connection_url()
    host = postgres.get_container_host_ip()
    port = postgres.get_exposed_port(5432)

    asyncio.run(_run_statements(admin_url, BOOTSTRAP))
    asyncio.run(
        _run_statements(admin_url.rsplit("/", 1)[0] + f"/{DB_NAME}", GRANTS)
    )

    settings = get_settings()
    settings.db_url = f"postgresql://{host}:{port}/{DB_NAME}"
    settings.db_user = APP_USER
    settings.db_password = ROLE_PASSWORD
    settings.db_owner_user = OWNER_USER
    settings.db_owner_password = ROLE_PASSWORD
    settings.openai_api_key = "test-key"

    asyncio.run(db.init_db())
    yield
    asyncio.run(db.reset_engines())


@pytest.fixture
async def api() -> AsyncIterator[AsyncClient]:
    """HTTP client that runs the app in *this* event loop.

    Starlette's TestClient spins up its own loop in a worker thread, which the
    shared SQLAlchemy engine cannot be used from. ASGITransport calls the app
    directly instead, so database work stays on one loop.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
async def clean_tables() -> None:
    """Reset mutable state between tests, keeping the seeded scenarios."""
    async with db.get_sessionmaker()() as session:
        await session.execute(text("DELETE FROM sessions"))
        await session.execute(text("DELETE FROM scenarios WHERE is_builtin = false"))
        await session.execute(
            text("UPDATE scenarios SET is_customized = false")
        )
        await session.execute(
            text(
                "UPDATE app_settings SET openai_api_key = NULL, realtime_model = NULL, "
                "analysis_model = NULL, scenario_assistant_model = NULL, "
                "realtime_voice = NULL, realtime_speed = NULL, "
                "realtime_help_speed_factor = NULL, realtime_vad_eagerness = NULL, "
                "wanikani_api_token = NULL, anki_deck_name = NULL, "
                "ankiconnect_url = NULL, transcription_model = NULL, tts_model = NULL"
            )
        )
        await session.commit()
