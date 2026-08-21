"""Scenario CRUD and the editor's writing assistant."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Scenario
from ..models import (
    ScenarioAssistantReply,
    ScenarioAssistantRequest,
    ScenarioCreate,
    ScenarioUpdate,
    ScenarioView,
)
from ..runtime_config import RuntimeConfig
from ..scenario_assistant import ScenarioAssistant, ScenarioAssistantError
from .deps import db_session, runtime_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def to_view(row: Scenario) -> ScenarioView:
    return ScenarioView(
        id=row.id,
        slug=row.slug,
        title=row.title,
        summary=row.summary,
        prompt=row.prompt,
        is_builtin=row.is_builtin,
        is_customized=row.is_customized,
    )


async def unique_slug(session: AsyncSession, title: str) -> str:
    """Derive a URL-safe slug from the title, suffixed until it is free."""
    base = SLUG_STRIP.sub("-", title.lower()).strip("-")[:48] or "scenario"
    candidate = base
    suffix = 2
    while await session.scalar(select(Scenario.id).where(Scenario.slug == candidate)):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


async def get_or_404(session: AsyncSession, scenario_id: int) -> Scenario:
    row = await session.get(Scenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return row


@router.get("", response_model=list[ScenarioView])
async def list_scenarios(session: AsyncSession = Depends(db_session)) -> list[ScenarioView]:
    rows = await session.scalars(
        select(Scenario).order_by(Scenario.sort_order, Scenario.title)
    )
    return [to_view(row) for row in rows]


@router.post("", response_model=ScenarioView, status_code=201)
async def create_scenario(
    payload: ScenarioCreate, session: AsyncSession = Depends(db_session)
) -> ScenarioView:
    # New scenarios sort after the built-in ones without renumbering them.
    highest = await session.scalar(select(func.max(Scenario.sort_order)))
    row = Scenario(
        slug=await unique_slug(session, payload.title),
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        prompt=payload.prompt.strip(),
        is_builtin=False,
        is_customized=True,
        sort_order=(highest or 0) + 1,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_view(row)


@router.put("/{scenario_id}", response_model=ScenarioView)
async def update_scenario(
    scenario_id: int,
    payload: ScenarioUpdate,
    session: AsyncSession = Depends(db_session),
) -> ScenarioView:
    """Edit a scenario.

    Editing marks it ``is_customized``, which is what stops the next boot from
    seeding the Markdown version back over the user's wording.
    """
    row = await get_or_404(session, scenario_id)
    provided = payload.model_dump(exclude_unset=True)

    values: dict[str, object] = {"is_customized": True, "updated_at": func.now()}
    for field in ("title", "summary", "prompt"):
        if field in provided and provided[field] is not None:
            values[field] = str(provided[field]).strip()

    await session.execute(update(Scenario).where(Scenario.id == row.id).values(**values))
    await session.commit()
    return to_view(await get_or_404(session, scenario_id))


@router.delete("/{scenario_id}", status_code=204)
async def delete_scenario(
    scenario_id: int, session: AsyncSession = Depends(db_session)
) -> None:
    """Remove a scenario. Past sessions keep their copy of title and prompt."""
    await get_or_404(session, scenario_id)
    await session.execute(delete(Scenario).where(Scenario.id == scenario_id))
    await session.commit()


@router.post("/{scenario_id}/reset", response_model=ScenarioView)
async def reset_scenario(
    scenario_id: int, session: AsyncSession = Depends(db_session)
) -> ScenarioView:
    """Restore a built-in scenario from its Markdown file on the next boot.

    Clearing ``is_customized`` is enough: seeding runs at startup and will
    overwrite the row. Doing it immediately would need the file loader here,
    so instead the file content is re-applied right away from the same source.
    """
    row = await get_or_404(session, scenario_id)
    if not row.is_builtin:
        raise HTTPException(
            status_code=400, detail="Only built-in scenarios can be reset."
        )

    from ..scenario_files import load_scenario_files

    original = next((f for f in load_scenario_files() if f.slug == row.slug), None)
    if original is None:
        raise HTTPException(
            status_code=404, detail="No Markdown file exists for this scenario."
        )

    await session.execute(
        update(Scenario)
        .where(Scenario.id == row.id)
        .values(
            title=original.title,
            summary=original.summary,
            prompt=original.prompt,
            is_customized=False,
            updated_at=func.now(),
        )
    )
    await session.commit()
    return to_view(await get_or_404(session, scenario_id))


@router.post("/assistant", response_model=ScenarioAssistantReply)
async def scenario_assistant(
    payload: ScenarioAssistantRequest,
    config: RuntimeConfig = Depends(runtime_config),
) -> ScenarioAssistantReply:
    """Ask the editor's assistant about the draft currently being written."""
    try:
        return await ScenarioAssistant(config).respond(payload)
    except ScenarioAssistantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
