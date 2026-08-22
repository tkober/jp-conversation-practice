"""Settings screen: read the effective configuration, patch the overrides."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import SETTINGS_ROW_ID, AppSettings, ensure_settings_row, load_settings
from ..model_catalog import SLOTS_BY_KEY, ModelCatalog, is_valid_model_id
from ..models import SettingsUpdate, SettingsView
from ..runtime_config import RuntimeConfig, build_runtime_config
from ..turn_detection import is_valid_eagerness
from ..voices import is_valid_voice
from .deps import db_session, runtime_config

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Long-lived so the live model list stays cached between requests; the key is
# passed per call because it can change on this very screen.
_catalog = ModelCatalog()

# Fields the client may patch, mapped to their column on AppSettings.
PATCHABLE = (
    "openai_api_key",
    "realtime_model",
    "analysis_model",
    "scenario_assistant_model",
    "transcription_model",
    "tts_model",
    "realtime_voice",
    "realtime_speed",
    "realtime_vad_eagerness",
    "wanikani_api_token",
    "ankiconnect_url",
    "anki_deck_name",
)


def mask_secret(value: str) -> str | None:
    """A hint that identifies a key without disclosing it."""
    if not value:
        return None
    tail = value[-4:] if len(value) > 8 else ""
    return f"…{tail}" if tail else "…"


def to_view(config: RuntimeConfig, row: AppSettings | None) -> SettingsView:
    """Render the effective settings without leaking either secret."""
    return SettingsView(
        realtime_model=config.realtime_model,
        analysis_model=config.analysis_model,
        scenario_assistant_model=config.scenario_assistant_model,
        transcription_model=config.transcription_model,
        tts_model=config.tts_model,
        realtime_voice=config.realtime_voice,
        realtime_speed=config.realtime_speed,
        realtime_vad_eagerness=config.realtime_vad_eagerness,
        ankiconnect_url=config.ankiconnect_url,
        anki_deck_name=config.anki_deck_name,
        openai_api_key_set=bool(config.openai_api_key),
        openai_api_key_hint=mask_secret(config.openai_api_key),
        # Distinguishing the source matters: a key from the environment cannot
        # be cleared in the UI, and saying so avoids a confusing dead end.
        openai_api_key_from_env=not (row and row.openai_api_key),
        wanikani_api_token_set=bool(config.wanikani_api_token),
        wanikani_api_token_hint=mask_secret(config.wanikani_api_token),
        wanikani_api_token_from_env=not (row and row.wanikani_api_token),
        speed_min=config.realtime_speed_min,
        speed_max=config.realtime_speed_max,
    )


@router.get("", response_model=SettingsView)
async def read_settings(session: AsyncSession = Depends(db_session)) -> SettingsView:
    row = await load_settings(session)
    return to_view(build_runtime_config(row, get_settings()), row)


@router.put("", response_model=SettingsView)
async def write_settings(
    patch: SettingsUpdate, session: AsyncSession = Depends(db_session)
) -> SettingsView:
    """Apply a partial update.

    Omitted fields stay as they are; an explicit empty value clears the
    override so the environment default takes over again.
    """
    values: dict[str, object] = {}
    provided = patch.model_dump(exclude_unset=True)

    for field in PATCHABLE:
        if field not in provided:
            continue
        value = provided[field]
        if isinstance(value, str):
            value = value.strip() or None
        if field == "realtime_voice" and value is not None and not is_valid_voice(str(value)):
            # Silently dropping is friendlier than a 422 here: the picker only
            # ever sends known ids, so this guards a hand-crafted request.
            continue
        if (
            field == "realtime_vad_eagerness"
            and value is not None
            and not is_valid_eagerness(str(value))
        ):
            continue
        if field in SLOTS_BY_KEY and value is not None and not is_valid_model_id(str(value)):
            # The dropdown's free-text escape hatch accepts any model name, so
            # the shape is checked here: tts_model becomes a directory under
            # `.voice-samples/`, and a name with a slash in it would write
            # outside the cache.
            continue
        values[field] = value

    if values:
        await ensure_settings_row(session)
        await session.execute(
            update(AppSettings).where(AppSettings.id == SETTINGS_ROW_ID).values(**values)
        )
        await session.commit()

    row = await load_settings(session)
    return to_view(build_runtime_config(row, get_settings()), row)


@router.get("/models")
async def read_models(config: RuntimeConfig = Depends(runtime_config)) -> dict[str, object]:
    """Dropdown contents for every configurable model slot."""
    catalog = await _catalog.build(config.openai_api_base, config.openai_api_key)
    return catalog.as_dict()
