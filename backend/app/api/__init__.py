"""HTTP and WebSocket routes, grouped by area."""

from __future__ import annotations

from fastapi import APIRouter

from . import practice, scenarios, sessions, settings

router = APIRouter()
router.include_router(practice.router)
router.include_router(settings.router)
router.include_router(scenarios.router)
router.include_router(sessions.router)

__all__ = ["router"]
