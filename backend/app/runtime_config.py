"""The effective configuration for one request or session.

Two layers: the environment (``.env`` / compose ``env_file``) provides the
defaults a fresh deployment boots with, and the ``app_settings`` row overrides
anything the user changes in the Settings screen. A NULL column means "not set
here", so removing a value in the UI falls back to the environment rather than
blanking the setting.

Loaded fresh per request instead of cached: the table has one row, and a stale
API key after a settings change would be far more annoying than the lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import AppSettings, load_settings
from .turn_detection import normalise_eagerness


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything the app needs at runtime, with overrides already applied."""

    # --- user-editable ---
    openai_api_key: str
    realtime_model: str
    analysis_model: str
    scenario_assistant_model: str
    transcription_model: str
    tts_model: str
    realtime_voice: str
    realtime_speed: float
    realtime_vad_eagerness: str
    wanikani_api_token: str
    ankiconnect_url: str
    anki_deck_name: str

    # --- environment only (infrastructure, not user business) ---
    openai_api_base: str
    openai_realtime_url: str
    wanikani_api_base: str
    wanikani_known_srs_stage: int
    audio_sample_rate: int
    realtime_speed_min: float
    realtime_speed_max: float
    realtime_beta_header: bool
    realtime_max_frame_bytes: int
    voice_sample_cache_dir: str

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def wanikani_configured(self) -> bool:
        return bool(self.wanikani_api_token)


def _pick(override: str | None, fallback: str) -> str:
    """Prefer a non-empty database value, else the environment default."""
    value = (override or "").strip()
    return value or fallback


def build_runtime_config(row: AppSettings | None, env: Settings) -> RuntimeConfig:
    """Merge the settings row onto the environment defaults."""
    speed = env.realtime_speed
    if row is not None and row.realtime_speed is not None:
        speed = row.realtime_speed

    return RuntimeConfig(
        openai_api_key=_pick(row and row.openai_api_key, env.openai_api_key),
        realtime_model=_pick(row and row.realtime_model, env.realtime_model),
        analysis_model=_pick(row and row.analysis_model, env.analysis_model),
        scenario_assistant_model=_pick(
            row and row.scenario_assistant_model, env.scenario_assistant_model
        ),
        transcription_model=_pick(row and row.transcription_model, env.transcription_model),
        tts_model=_pick(row and row.tts_model, env.tts_model),
        realtime_voice=_pick(row and row.realtime_voice, env.realtime_voice),
        realtime_speed=max(env.realtime_speed_min, min(env.realtime_speed_max, speed)),
        realtime_vad_eagerness=normalise_eagerness(
            row and row.realtime_vad_eagerness,
            normalise_eagerness(env.realtime_vad_eagerness),
        ),
        wanikani_api_token=_pick(row and row.wanikani_api_token, env.wanikani_api_token),
        ankiconnect_url=_pick(row and row.ankiconnect_url, env.ankiconnect_url),
        anki_deck_name=_pick(row and row.anki_deck_name, env.anki_deck_name),
        openai_api_base=env.openai_api_base,
        openai_realtime_url=env.openai_realtime_url,
        wanikani_api_base=env.wanikani_api_base,
        wanikani_known_srs_stage=env.wanikani_known_srs_stage,
        audio_sample_rate=env.audio_sample_rate,
        realtime_speed_min=env.realtime_speed_min,
        realtime_speed_max=env.realtime_speed_max,
        realtime_beta_header=env.realtime_beta_header,
        realtime_max_frame_bytes=env.realtime_max_frame_bytes,
        voice_sample_cache_dir=env.voice_sample_cache_dir,
    )


async def load_runtime_config(session: AsyncSession) -> RuntimeConfig:
    """Read the settings row and merge it onto the environment."""
    return build_runtime_config(await load_settings(session), get_settings())
