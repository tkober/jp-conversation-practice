"""Available tutor voices and their audio previews.

The voice can only be chosen *before* the session produces audio -- the Realtime
API fixes it for the rest of the session -- so the picker lives on the setup
screen. Previews are rendered through the TTS endpoint and cached on disk, so
each voice costs a fraction of a cent once and nothing afterwards.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

# Sentence used for every preview. Short, natural, and typical of what the tutor
# actually says, so the samples are comparable.
SAMPLE_TEXT = "こんにちは。今日は日本語で少し話しましょう。"

SAMPLE_INSTRUCTIONS = (
    "Speak as a warm, encouraging Japanese language tutor talking to a beginner: "
    "clear, friendly, and a little slower than native pace."
)

VOICE_ID_PATTERN = re.compile(r"^[a-z_-]{1,32}$")


@dataclass(frozen=True)
class Voice:
    """One selectable tutor voice.

    `description` is user-facing and therefore German, like the scenario titles.
    """

    id: str
    label: str
    description: str


# Voices available to both the Realtime API and the TTS endpoint, so a preview
# is representative of what the tutor will sound like.
VOICES: list[Voice] = [
    Voice("marin", "Marin", "Weiblich, warm und ruhig — Standard"),
    Voice("cedar", "Cedar", "Männlich, ruhig und deutlich"),
    Voice("alloy", "Alloy", "Neutral und sachlich"),
    Voice("ash", "Ash", "Männlich, tiefer und gelassen"),
    Voice("ballad", "Ballad", "Männlich, weich und erzählend"),
    Voice("coral", "Coral", "Weiblich, hell und lebhaft"),
    Voice("echo", "Echo", "Männlich, nüchtern und klar"),
    Voice("sage", "Sage", "Weiblich, gelassen und freundlich"),
    Voice("shimmer", "Shimmer", "Weiblich, weich und leise"),
    Voice("verse", "Verse", "Männlich, ausdrucksstark"),
]

VOICE_IDS = frozenset(voice.id for voice in VOICES)


class VoiceSampleError(RuntimeError):
    """Raised when a voice preview cannot be produced."""


def is_valid_voice(voice: str) -> bool:
    return voice in VOICE_IDS


class VoiceSampleService:
    """Renders and caches one preview clip per voice."""

    def __init__(self, cache_dir: Path, api_base: str, api_key: str, model: str) -> None:
        self.cache_dir = cache_dir
        self.api_base = api_base
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> VoiceSampleService:
        """Build a service from the effective settings.

        Cheap to construct per request: the cache that matters is on disk, so
        nothing is lost by not keeping the instance around. The cache path is
        per TTS model, because the same voice sounds different across models.
        """
        return cls(
            Path(config.voice_sample_cache_dir) / config.tts_model,
            config.openai_api_base,
            config.openai_api_key,
            config.tts_model,
        )

    def cached_path(self, voice: str) -> Path:
        return self.cache_dir / f"{voice}.wav"

    async def get_sample(self, voice: str) -> bytes:
        """Return the preview for ``voice``, rendering it on first request."""
        if not is_valid_voice(voice):
            # Defence in depth: the id also lands in a filesystem path.
            raise VoiceSampleError(f"Unknown voice '{voice}'.")

        path = self.cached_path(voice)
        if path.exists():
            return path.read_bytes()

        if not self.api_key:
            raise VoiceSampleError("OPENAI_API_KEY is not configured on the server.")

        audio = await self._render(voice)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
        except OSError as exc:
            # A read-only cache directory must not break the preview itself.
            logger.warning("Could not cache voice sample for %s: %s", voice, exc)
        return audio

    async def _render(self, voice: str) -> bytes:
        url = f"{self.api_base.rstrip('/')}/audio/speech"
        payload = {
            "model": self.model,
            "voice": voice,
            "input": SAMPLE_TEXT,
            "instructions": SAMPLE_INSTRUCTIONS,
            "response_format": "wav",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {self.api_key}"}
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:200]
            logger.error("Voice sample for %s failed: %s", voice, detail)
            raise VoiceSampleError(
                f"Could not render a sample for '{voice}' (HTTP {exc.response.status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise VoiceSampleError(f"Could not reach the OpenAI API: {exc}") from exc
