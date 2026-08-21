"""FastAPI application: realtime relay, post-session analysis and Anki export."""

from __future__ import annotations

import logging

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from .analysis import AnalysisError, AnalysisService, filter_known_cards
from .anki import AnkiConnectClient, AnkiConnectError
from .config import Settings, get_settings
from .models import (
    AnalysisRequest,
    AnalysisResponse,
    AnkiExportRequest,
    AnkiExportResponse,
)
from .realtime import RealtimeSession
from .scenarios import SCENARIO_PRESETS
from .voices import VOICES, VoiceSampleError, VoiceSampleService
from .wanikani import WaniKaniClient, WaniKaniError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Japanese Conversation Practice PoC", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Long-lived clients; the WaniKani one caches the vocabulary list in process.
_wanikani = WaniKaniClient(
    settings.wanikani_api_token,
    settings.wanikani_api_base,
    settings.wanikani_known_srs_stage,
)
_analysis = AnalysisService(settings)
_voice_samples = VoiceSampleService(
    Path(settings.voice_sample_cache_dir),
    settings.openai_api_base,
    settings.openai_api_key,
    settings.tts_model,
)


def get_current_settings() -> Settings:
    return settings


@app.get("/")
async def root() -> dict[str, str]:
    """Signpost for anyone who opens the backend port directly."""
    return {
        "service": "Japanese Conversation Practice PoC -- backend",
        "frontend": "http://localhost:4200",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health")
async def health(config: Settings = Depends(get_current_settings)) -> dict[str, object]:
    """Report which integrations are configured, for the frontend's setup hints."""
    return {
        "status": "ok",
        "openai_configured": bool(config.openai_api_key),
        "wanikani_configured": _wanikani.enabled,
        "realtime_model": config.realtime_model,
        "analysis_model": config.analysis_model,
        "sample_rate": config.audio_sample_rate,
        "anki_deck_name": config.anki_deck_name,
    }


@app.get("/api/scenarios")
async def scenarios() -> dict[str, object]:
    """Preset scenarios offered in the setup screen."""
    return {"scenarios": SCENARIO_PRESETS}


@app.get("/api/voices")
async def voices(config: Settings = Depends(get_current_settings)) -> dict[str, object]:
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


@app.get("/api/voices/{voice_id}/sample")
async def voice_sample(voice_id: str) -> Response:
    """Return a short spoken preview of one voice, cached after first render."""
    try:
        audio = await _voice_samples.get_sample(voice_id)
    except VoiceSampleError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.websocket("/ws/realtime")
async def realtime_endpoint(websocket: WebSocket) -> None:
    """Relay one conversation between the browser and the OpenAI Realtime API."""
    session = RealtimeSession(websocket, settings)
    await session.run()


@app.post("/api/analysis", response_model=AnalysisResponse)
async def analyse_session(request: AnalysisRequest) -> AnalysisResponse:
    """Turn a finished transcript into feedback, grammar notes and Anki cards."""
    if not request.transcript:
        raise HTTPException(status_code=400, detail="The transcript is empty.")

    known_words: set[str] = set()
    wanikani_status = "disabled"
    wanikani_message: str | None = None

    if request.use_wanikani_filter and _wanikani.enabled:
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
        analysis = await _analysis.analyse(
            scenario=request.scenario,
            jlpt_level=request.jlpt_level,
            transcript=request.transcript,
            excluded_words=sorted(known_words),
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


@app.post("/api/anki/export", response_model=AnkiExportResponse)
async def export_to_anki(request: AnkiExportRequest) -> AnkiExportResponse:
    """Push the selected cards into the local Anki desktop app via AnkiConnect."""
    if not request.cards:
        raise HTTPException(status_code=400, detail="No cards to export.")

    deck_name = request.deck_name or settings.anki_deck_name
    client = AnkiConnectClient(settings.ankiconnect_url, settings.anki_model_name)

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
