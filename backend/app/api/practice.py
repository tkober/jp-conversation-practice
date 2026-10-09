"""Health, voices, post-session analysis and the Anki export."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.responses import Response

from ..analysis import AnalysisError, AnalysisService, filter_known_cards
from ..anki import AnkiConnectClient, AnkiConnectError
from ..config import get_settings
from ..db import get_sessionmaker
from ..models import (
    AnalysisRequest,
    AnalysisResponse,
    AnkiExportRequest,
    AnkiExportResponse,
)
from ..realtime import RealtimeSession
from ..runtime_config import RuntimeConfig, load_runtime_config
from ..voices import VOICES, VoiceSampleError, VoiceSampleService
from ..wanikani import WaniKaniClient, WaniKaniError
from .deps import runtime_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["practice"])

# Long-lived so the vocabulary list stays cached between requests; the token is
# re-applied per call because it can change in the Settings screen.
_wanikani = WaniKaniClient()


@router.get("/")
async def root() -> dict[str, str]:
    """Signpost for anyone who opens the backend port directly."""
    return {
        "service": "Japanese Conversation Practice -- backend",
        "docs": "/docs",
        "health": "/api/health",
    }


@router.get("/api/health")
async def health(config: RuntimeConfig = Depends(runtime_config)) -> dict[str, object]:
    """Report which integrations are configured, for the UI's setup hints."""
    return {
        "status": "ok",
        "openai_configured": config.openai_configured,
        "wanikani_configured": config.wanikani_configured,
        "realtime_model": config.realtime_model,
        "analysis_model": config.analysis_model,
        "sample_rate": config.audio_sample_rate,
        "anki_deck_name": config.anki_deck_name,
    }


@router.get("/api/voices")
async def voices(config: RuntimeConfig = Depends(runtime_config)) -> dict[str, object]:
    """Selectable tutor voices, plus the speed range the UI may offer."""
    return {
        "voices": [
            {"id": voice.id, "label": voice.label, "description": voice.description}
            for voice in VOICES
        ],
        "default_voice": config.realtime_voice,
        "default_speed": config.realtime_speed,
        "speed_min": config.realtime_speed_min,
        "speed_max": config.realtime_speed_max,
    }


@router.get("/api/voices/{voice_id}/sample")
async def voice_sample(
    voice_id: str, config: RuntimeConfig = Depends(runtime_config)
) -> Response:
    """A short spoken preview of one voice, cached after the first render."""
    try:
        audio = await VoiceSampleService.from_config(config).get_sample(voice_id)
    except VoiceSampleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def session_config() -> RuntimeConfig:
    """Read the effective settings for a new realtime session.

    Separate from the endpoint because a WebSocket route cannot use FastAPI's
    dependency overrides, and the relay tests need to supply a config without
    a database.
    """
    async with get_sessionmaker()() as session:
        return await load_runtime_config(session)


@router.websocket("/ws/realtime")
async def realtime_endpoint(websocket: WebSocket) -> None:
    """Relay one conversation between the browser and the OpenAI Realtime API.

    The settings are read once, before the handshake: a change made mid-session
    should not reconfigure a conversation that is already running.
    """
    config = await session_config()
    await RealtimeSession(websocket, config).run()


@router.post("/api/analysis", response_model=AnalysisResponse)
async def analyse_session(
    request: AnalysisRequest, config: RuntimeConfig = Depends(runtime_config)
) -> AnalysisResponse:
    """Turn a finished transcript into feedback, grammar notes and Anki cards."""
    if not request.transcript:
        raise HTTPException(status_code=400, detail="The transcript is empty.")

    known_words: set[str] = set()
    wanikani_status = "disabled"
    wanikani_message: str | None = None

    if request.use_wanikani_filter and config.wanikani_configured:
        _wanikani.configure(
            config.wanikani_api_token,
            config.wanikani_api_base,
            config.wanikani_known_srs_stage,
        )
        try:
            known_words = await _wanikani.get_known_vocabulary()
            wanikani_status = "ok"
            wanikani_message = f"{len(known_words)} known words loaded from WaniKani."
        except WaniKaniError as exc:
            # A WaniKani outage must not cost the user their analysis.
            wanikani_status = "error"
            wanikani_message = str(exc)
            logger.warning("WaniKani filter unavailable: %s", exc)

    try:
        analysis = await AnalysisService(config).analyse(
            scenario=request.scenario,
            jlpt_level=request.jlpt_level,
            transcript=request.transcript,
            excluded_words=sorted(known_words),
            context_items=request.context_items,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    filtered, removed = filter_known_cards(analysis, known_words)

    return AnalysisResponse(
        **filtered.model_dump(),
        filtered_out=removed,
        wanikani_status=wanikani_status,
        wanikani_message=wanikani_message,
    )


@router.post("/api/anki/export", response_model=AnkiExportResponse)
async def export_to_anki(
    request: AnkiExportRequest, config: RuntimeConfig = Depends(runtime_config)
) -> AnkiExportResponse:
    """Push the selected cards into the local Anki desktop app via AnkiConnect."""
    if not request.cards:
        raise HTTPException(status_code=400, detail="No cards to export.")

    deck_name = request.deck_name or config.anki_deck_name
    client = AnkiConnectClient(config.ankiconnect_url, get_settings().anki_model_name)

    try:
        note_ids, duplicates = await client.add_cards(request.cards, deck_name, request.tags)
    except AnkiConnectError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AnkiExportResponse(
        added=len(note_ids) - duplicates,
        duplicates=duplicates,
        deck_name=deck_name,
        note_ids=note_ids,
    )
