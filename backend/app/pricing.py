"""Exact cost calculation for OpenAI Realtime API sessions.

Costs are derived from the ``usage`` object that the Realtime API reports on
every ``response.done`` event, so the numbers are exact rather than estimated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TOKENS_PER_UNIT = 1_000_000


@dataclass(frozen=True)
class ModelRates:
    """USD price per 1M tokens for a realtime model."""

    text_input: float
    text_cached_input: float
    text_output: float
    audio_input: float
    audio_cached_input: float
    audio_output: float


# Published rates (USD per 1M tokens). Keep this table in sync with
# https://openai.com/api/pricing/ -- the PoC hard-codes them on purpose so the
# live counter never depends on a network round trip.
MODEL_RATES: dict[str, ModelRates] = {
    "gpt-realtime-2.1-mini": ModelRates(
        text_input=0.60,
        text_cached_input=0.06,
        text_output=2.40,
        audio_input=10.00,
        audio_cached_input=0.30,
        audio_output=20.00,
    ),
    "gpt-realtime-mini": ModelRates(
        text_input=0.60,
        text_cached_input=0.06,
        text_output=2.40,
        audio_input=10.00,
        audio_cached_input=0.30,
        audio_output=20.00,
    ),
    "gpt-realtime": ModelRates(
        text_input=4.00,
        text_cached_input=0.40,
        text_output=16.00,
        audio_input=32.00,
        audio_cached_input=0.40,
        audio_output=64.00,
    ),
    # The versioned full-size models. Audio is priced identically to
    # `gpt-realtime` across all of them -- what moved between 1.5 and 2 is text
    # output, the small component in a spoken session.
    "gpt-realtime-1.5": ModelRates(
        text_input=4.00,
        text_cached_input=0.40,
        text_output=16.00,
        audio_input=32.00,
        audio_cached_input=0.40,
        audio_output=64.00,
    ),
    "gpt-realtime-2": ModelRates(
        text_input=4.00,
        text_cached_input=0.40,
        text_output=24.00,
        audio_input=32.00,
        audio_cached_input=0.40,
        audio_output=64.00,
    ),
    "gpt-realtime-2.1": ModelRates(
        text_input=4.00,
        text_cached_input=0.40,
        text_output=24.00,
        audio_input=32.00,
        audio_cached_input=0.40,
        audio_output=64.00,
    ),
}

DEFAULT_RATES = MODEL_RATES["gpt-realtime-2.1-mini"]


def rates_for(model: str) -> ModelRates:
    """Return the price table for ``model``, falling back to the mini rates."""
    return MODEL_RATES.get(model, DEFAULT_RATES)


@dataclass
class TokenBucket:
    """Cumulative token counters for one direction of the conversation."""

    text_tokens: int = 0
    cached_text_tokens: int = 0
    audio_tokens: int = 0
    cached_audio_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "text_tokens": self.text_tokens,
            "cached_text_tokens": self.cached_text_tokens,
            "audio_tokens": self.audio_tokens,
            "cached_audio_tokens": self.cached_audio_tokens,
        }


def _as_int(value: Any) -> int:
    """Coerce a usage field to int, tolerating nulls and floats."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


class CostTracker:
    """Accumulates exact token usage and USD cost across a realtime session."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.rates = rates_for(model)
        self.input = TokenBucket()
        self.output = TokenBucket()
        self.total_tokens = 0
        self.response_count = 0

    def add_usage(self, usage: dict[str, Any]) -> None:
        """Fold one ``response.done`` usage object into the running totals.

        The Realtime API reports flat ``input_tokens`` / ``output_tokens``
        counters plus a ``*_token_details`` breakdown. The breakdown is what we
        bill against, because audio and text tokens are priced very
        differently. Cached input tokens are reported *inside*
        ``input_token_details`` and are a subset of the audio/text counts, so
        they are subtracted before the uncached rate is applied.
        """
        if not isinstance(usage, dict):
            return

        self.response_count += 1
        self.total_tokens += _as_int(usage.get("total_tokens"))

        input_details = usage.get("input_token_details") or {}
        cached_details = input_details.get("cached_tokens_details") or {}

        if cached_details:
            cached_text = _as_int(cached_details.get("text_tokens"))
            cached_audio = _as_int(cached_details.get("audio_tokens"))
        else:
            # Older payload shape: only a flat `cached_tokens` counter exists.
            # Attribute it to audio, which dominates a realtime session.
            cached_text = 0
            cached_audio = _as_int(input_details.get("cached_tokens"))

        self.input.text_tokens += _as_int(input_details.get("text_tokens"))
        self.input.audio_tokens += _as_int(input_details.get("audio_tokens"))
        self.input.cached_text_tokens += cached_text
        self.input.cached_audio_tokens += cached_audio

        output_details = usage.get("output_token_details") or {}
        self.output.text_tokens += _as_int(output_details.get("text_tokens"))
        self.output.audio_tokens += _as_int(output_details.get("audio_tokens"))

    @property
    def cost_usd(self) -> float:
        """Total session cost in USD."""
        rates = self.rates
        uncached_input_text = max(self.input.text_tokens - self.input.cached_text_tokens, 0)
        uncached_input_audio = max(self.input.audio_tokens - self.input.cached_audio_tokens, 0)

        billed = (
            uncached_input_text * rates.text_input
            + self.input.cached_text_tokens * rates.text_cached_input
            + uncached_input_audio * rates.audio_input
            + self.input.cached_audio_tokens * rates.audio_cached_input
            + self.output.text_tokens * rates.text_output
            + self.output.audio_tokens * rates.audio_output
        )
        return billed / TOKENS_PER_UNIT

    def snapshot(self) -> dict[str, Any]:
        """Serializable view of the tracker, pushed to the frontend."""
        return {
            "model": self.model,
            "response_count": self.response_count,
            "total_tokens": self.total_tokens,
            "input": self.input.as_dict(),
            "output": self.output.as_dict(),
            "cost_usd": round(self.cost_usd, 6),
            "rates_known": self.model in MODEL_RATES,
        }
