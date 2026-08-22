"""Explain the one way of running pytest that silently does the wrong thing.

The test settings -- pytest-asyncio's ``auto`` mode above all -- live in
``backend/pyproject.toml``. A bare ``pytest`` from the repository root looks
for configuration in the root and above it, finds none, and falls back to
asyncio strict mode. pytest-asyncio then refuses to handle the async fixtures
in ``backend/tests``, and every test in the suite dies in fixture setup with an
``AssertionError`` that says nothing about the cause -- sixty-odd times.

Saying it once, in a sentence naming the fix, is worth this file.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.inipath is None:
        raise pytest.UsageError(
            "No pytest configuration was loaded, so the tests would run in "
            "asyncio strict mode and every async fixture would fail. The "
            "settings live in backend/pyproject.toml: run the suite from "
            "there (cd backend && uv run pytest) or name the path "
            "(pytest backend/tests)."
        )
