"""Session history: store finished conversations, list and inspect them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Session as SessionRow
from ..models import SessionCreate, SessionDetail, SessionSummary
from .deps import db_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

MAX_PAGE_SIZE = 200


def to_summary(row: SessionRow) -> SessionSummary:
    return SessionSummary(
        id=row.id,
        scenario_title=row.scenario_title,
        jlpt_level=row.jlpt_level,
        model=row.model,
        voice=row.voice,
        started_at=row.started_at,
        duration_seconds=row.duration_seconds,
        cost_usd=row.cost_usd,
        turn_count=len(row.transcript or []),
        has_analysis=row.analysis is not None,
    )


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(db_session),
) -> list[SessionSummary]:
    """Newest first. Transcripts are omitted — they make the list heavy."""
    rows = await session.scalars(
        select(SessionRow)
        .order_by(SessionRow.started_at.desc())
        .limit(max(1, min(limit, MAX_PAGE_SIZE)))
        .offset(max(0, offset))
    )
    return [to_summary(row) for row in rows]


@router.get("/stats")
async def session_stats(session: AsyncSession = Depends(db_session)) -> dict[str, float]:
    """Totals for the history header."""
    row = (
        await session.execute(
            select(
                func.count(SessionRow.id),
                func.coalesce(func.sum(SessionRow.cost_usd), 0.0),
                func.coalesce(func.sum(SessionRow.duration_seconds), 0.0),
            )
        )
    ).one()
    return {
        "session_count": int(row[0]),
        "total_cost_usd": round(float(row[1]), 6),
        "total_seconds": float(row[2]),
    }


@router.get("/{session_id}", response_model=SessionDetail)
async def read_session(
    session_id: int, session: AsyncSession = Depends(db_session)
) -> SessionDetail:
    row = await session.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return SessionDetail(
        **to_summary(row).model_dump(),
        scenario_prompt=row.scenario_prompt,
        speed=row.speed,
        vad_eagerness=row.vad_eagerness,
        instructions=row.instructions,
        usage=row.usage or {},
        transcript=row.transcript or [],
        analysis=row.analysis,
    )


@router.post("", response_model=SessionSummary, status_code=201)
async def create_session(
    payload: SessionCreate, session: AsyncSession = Depends(db_session)
) -> SessionSummary:
    """Persist a finished conversation.

    The scenario's title and prompt are stored on the row rather than only
    referenced: a session records what actually happened, so editing or
    deleting the scenario later must not rewrite history.
    """
    row = SessionRow(
        scenario_id=payload.scenario_id,
        scenario_title=payload.scenario_title,
        scenario_prompt=payload.scenario_prompt,
        jlpt_level=payload.jlpt_level,
        model=payload.model,
        voice=payload.voice,
        speed=payload.speed,
        vad_eagerness=payload.vad_eagerness,
        instructions=payload.instructions,
        duration_seconds=payload.duration_seconds,
        cost_usd=payload.cost_usd,
        usage=payload.usage,
        transcript=[turn.model_dump() for turn in payload.transcript],
        analysis=payload.analysis,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return to_summary(row)


@router.put("/{session_id}/analysis", response_model=SessionSummary)
async def attach_analysis(
    session_id: int,
    analysis: dict,
    session: AsyncSession = Depends(db_session),
) -> SessionSummary:
    """Attach the analysis result, which arrives after the session is stored."""
    row = await session.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    row.analysis = analysis
    await session.commit()
    await session.refresh(row)
    return to_summary(row)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: int, session: AsyncSession = Depends(db_session)
) -> None:
    row = await session.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    await session.execute(delete(SessionRow).where(SessionRow.id == session_id))
    await session.commit()
