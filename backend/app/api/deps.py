"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_sessionmaker
from ..runtime_config import RuntimeConfig, load_runtime_config


async def db_session() -> AsyncIterator[AsyncSession]:
    """One app-role session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def runtime_config(
    session: AsyncSession = Depends(db_session),
) -> RuntimeConfig:
    """The effective settings: environment defaults with database overrides."""
    return await load_runtime_config(session)
