"""Startup behaviour: wait for a database that is booting, fail fast on a
misconfigured one."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app import db
from app.config import get_settings


class FakeError(Exception):
    """Stands in for a driver error carrying a SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


@pytest.fixture(autouse=True)
def postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting and SQLSTATEs are Postgres's rules.

    A SQLite file has nothing to boot and no SQLSTATE to read, and takes its
    own branch (covered at the bottom of this file), so ``db_url`` decides
    which of the two is under test here. Pinning it keeps these cases about
    Postgres whichever database the suite itself runs on.
    """
    monkeypatch.setattr(
        get_settings(), "db_url", "postgresql://localhost:5432/jp_conversation"
    )


def stand_in_engine():
    """An engine for ``_wait_for_database`` to hold.

    It only ever runs ``SELECT 1`` on it, so what the engine points at does not
    matter -- and an in-memory SQLite one answers without a server, which is
    what lets the "the database finally came up" case be tested at all.
    """
    return create_async_engine("sqlite+aiosqlite://", future=True)


async def test_waits_and_succeeds_once_the_database_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database that is still booting is worth waiting for."""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_connect_delay_seconds", 0)

    attempts = 0
    real_connect = db.AsyncEngine.connect

    def flaky_connect(self):  # noqa: ANN001 - patched method
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionRefusedError("connection refused")
        return real_connect(self)

    monkeypatch.setattr(db.AsyncEngine, "connect", flaky_connect)

    engine = stand_in_engine()
    try:
        await db._wait_for_database(engine)
    finally:
        await engine.dispose()

    assert attempts == 3


async def test_gives_up_after_the_configured_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "db_connect_delay_seconds", 0)
    monkeypatch.setattr(settings, "db_connect_attempts", 3)

    attempts = 0

    def always_refused(self):  # noqa: ANN001 - patched method
        nonlocal attempts
        attempts += 1
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(db.AsyncEngine, "connect", always_refused)

    engine = stand_in_engine()
    try:
        with pytest.raises(ConnectionRefusedError):
            await db._wait_for_database(engine)
    finally:
        await engine.dispose()

    assert attempts == 3


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        # Postgres uses one code for "no such role" and "wrong password", so
        # the message must not commit to either.
        ("28P01", "does not exist, or its password"),
        ("28000", "does not exist"),
        ("3D000", "does not exist"),
    ],
)
async def test_misconfiguration_fails_immediately_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str, expected: str
) -> None:
    """A wrong password never becomes right by waiting, so do not retry it."""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_connect_delay_seconds", 0)

    attempts = 0

    def rejected(self):  # noqa: ANN001 - patched method
        nonlocal attempts
        attempts += 1
        raise FakeError(sqlstate)

    monkeypatch.setattr(db.AsyncEngine, "connect", rejected)

    engine = stand_in_engine()
    try:
        with pytest.raises(db.DatabaseUnavailable) as caught:
            await db._wait_for_database(engine)
    finally:
        await engine.dispose()

    assert attempts == 1, "a fatal error must not be retried"
    assert expected in str(caught.value)
    # The message has to name what to change, not just what failed.
    message = str(caught.value)
    assert "dbeaver/" in message, "the message must name the file that fixes it"


async def test_wrapped_driver_errors_are_recognised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLAlchemy wraps driver errors; the SQLSTATE must still be found."""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_connect_delay_seconds", 0)

    class Wrapper(Exception):
        def __init__(self) -> None:
            super().__init__("wrapped")
            self.orig = FakeError("28P01")

    def rejected(self):  # noqa: ANN001 - patched method
        raise Wrapper()

    monkeypatch.setattr(db.AsyncEngine, "connect", rejected)

    engine = stand_in_engine()
    try:
        with pytest.raises(db.DatabaseUnavailable):
            await db._wait_for_database(engine)
    finally:
        await engine.dispose()


async def test_a_sqlite_file_is_not_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local file is openable now or never -- retrying it helps nobody."""
    settings = get_settings()
    monkeypatch.setattr(settings, "db_url", "sqlite:///:memory:")
    monkeypatch.setattr(settings, "db_connect_delay_seconds", 0)

    attempts = 0

    def refused(self):  # noqa: ANN001 - patched method
        nonlocal attempts
        attempts += 1
        raise OSError("unable to open database file")

    monkeypatch.setattr(db.AsyncEngine, "connect", refused)

    engine = create_async_engine(settings.owner_database_url, future=True)
    try:
        with pytest.raises(db.DatabaseUnavailable) as caught:
            await db._wait_for_database(engine)
    finally:
        await engine.dispose()

    assert attempts == 1
    # The message has to name the path, or you cannot tell which file failed.
    assert "SQLite" in str(caught.value)


async def test_a_sqlite_file_opens_without_ceremony(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path / 'probe.db'}")

    engine = create_async_engine(settings.owner_database_url, future=True)
    try:
        await db._wait_for_database(engine)
    finally:
        await engine.dispose()


def test_the_missing_directory_is_created(tmp_path: Path) -> None:
    """SQLite creates the file but not the folder, and blames the file."""
    target = tmp_path / "nested" / "deeper" / "jp.db"
    db._prepare_sqlite_directory(target)
    assert target.parent.is_dir()
