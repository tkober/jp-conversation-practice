"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


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
    # Assists in the scenario editor; separate because it writes prose rather
    # than driving a live conversation, so a stronger model can be worth it.
    scenario_assistant_model: str = "gpt-4o"
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
    # How much the tutor slows down for a わからない turn, as a factor on the
    # current speed. Help that arrives at conversational pace is not much help;
    # 0.8 is noticeably slower without tipping into the drawn-out delivery that
    # makes a sentence harder to parse, not easier. 1.0 switches it off.
    realtime_help_speed_factor: float = 0.8
    realtime_help_speed_factor_min: float = 0.5
    realtime_help_speed_factor_max: float = 1.0
    # How readily the semantic VAD treats a pause as the end of the learner's
    # turn -- see turn_detection.py for why "low" is the default. The Settings
    # screen overrides this, and the session screen can change it live.
    realtime_vad_eagerness: str = "low"
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

    # --- Content ---
    # Directory holding the built-in scenario Markdown files. The image sets an
    # absolute path; relative values resolve against the backend directory.
    scenarios_dir: str = "scenarios"

    # --- Database ---
    # DB_URL carries only host/port/database; credentials come per role, the
    # same split the other stacks on postgres-core use. The owner role runs DDL
    # at startup, the app role serves every request.
    #
    # A `sqlite://` URL switches the whole thing to a local file instead --
    # for a deployment that has no Postgres to point at. The roles are then
    # ignored: SQLite's access control is the filesystem's.
    db_url: str = "postgresql://localhost:5432/jp_conversation"
    db_user: str = "jp_conversation_app"
    db_password: str = ""
    db_owner_user: str = "jp_conversation_owner"
    db_owner_password: str = ""
    # postgres-core lives in its own compose stack, so `depends_on` cannot
    # order this one after it. On a host reboot both come up at once and the
    # database may not accept connections for a while yet.
    db_connect_attempts: int = 30
    db_connect_delay_seconds: float = 2.0

    # --- Server ---
    cors_origins: str = "http://localhost:4200"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def uses_sqlite(self) -> bool:
        """True when DB_URL points at a file rather than at a Postgres server."""
        return make_url(self.db_url).get_backend_name() == "sqlite"

    @property
    def sqlite_path(self) -> Path | None:
        """Where the SQLite file lives, or None when Postgres is configured.

        None also covers the in-memory forms (``sqlite://`` and
        ``sqlite:///:memory:``) -- there is no file to create a directory for.
        """
        if not self.uses_sqlite:
            return None
        database = make_url(self.db_url).database
        if not database or database == ":memory:":
            return None
        return Path(database)

    def _role_url(self, user: str, password: str) -> URL:
        """Build an async SQLAlchemy URL for one role from the base DB_URL.

        SQLite has no roles, so both roles resolve to the same file: the
        owner/app split is a Postgres privilege boundary, and there is nothing
        on the SQLite side to enforce it with. What the split protects against
        -- a request path quietly issuing DDL -- is still caught by the tests,
        which run the Postgres roles for real.
        """
        url = make_url(self.db_url)
        if url.get_backend_name() == "sqlite":
            return url.set(drivername="sqlite+aiosqlite")
        return url.set(
            drivername="postgresql+asyncpg",
            username=user or None,
            password=password or None,
        )

    @property
    def app_database_url(self) -> URL:
        """The role serving requests -- CRUD only, no DDL."""
        return self._role_url(self.db_user, self.db_password)

    @property
    def owner_database_url(self) -> URL:
        """The role used at startup for DDL and seeding."""
        return self._role_url(self.db_owner_user, self.db_owner_password)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
