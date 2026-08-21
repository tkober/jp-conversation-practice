"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the PoC backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    realtime_model: str = "gpt-realtime-2.1-mini"
    analysis_model: str = "gpt-4o-mini"
    openai_api_base: str = "https://api.openai.com/v1"

    # --- Realtime audio ---
    # The Realtime API works with raw PCM16 mono at 24 kHz in both directions.
    audio_sample_rate: int = 24000
    realtime_voice: str = "marin"
    realtime_speed: float = 1.0
    # The Realtime API accepts 0.25-1.5; below ~0.6 speech starts to smear, and
    # above ~1.4 it is no longer useful for a learner.
    realtime_speed_min: float = 0.6
    realtime_speed_max: float = 1.4
    tts_model: str = "gpt-4o-mini-tts"
    voice_sample_cache_dir: str = ".voice-samples"
    transcription_model: str = "gpt-4o-mini-transcribe"
    # Pre-GA realtime models require the `OpenAI-Beta: realtime=v1` header.
    realtime_beta_header: bool = False
    realtime_max_frame_bytes: int = 16 * 1024 * 1024

    # --- WaniKani ---
    wanikani_api_token: str = ""
    wanikani_api_base: str = "https://api.wanikani.com/v2"
    # SRS stage 5 is "Guru I"; everything at or above that counts as "known".
    wanikani_known_srs_stage: int = 5

    # --- AnkiConnect ---
    ankiconnect_url: str = "http://localhost:8765"
    anki_deck_name: str = "Japanese::AI Conversation"
    anki_model_name: str = "JP Conversation PoC"

    # --- Server ---
    cors_origins: str = "http://localhost:4200"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
