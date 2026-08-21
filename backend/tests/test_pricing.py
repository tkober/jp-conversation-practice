"""Tests for the exact realtime cost accounting."""

from __future__ import annotations

from app.pricing import MODEL_RATES, CostTracker

MODEL = "gpt-realtime-2.1-mini"
RATES = MODEL_RATES[MODEL]


def usage_event(
    *,
    input_text: int = 0,
    input_audio: int = 0,
    cached_text: int = 0,
    cached_audio: int = 0,
    output_text: int = 0,
    output_audio: int = 0,
) -> dict:
    """Build a `response.done` usage object in the shape the API sends."""
    return {
        "total_tokens": input_text + input_audio + output_text + output_audio,
        "input_tokens": input_text + input_audio,
        "output_tokens": output_text + output_audio,
        "input_token_details": {
            "text_tokens": input_text,
            "audio_tokens": input_audio,
            "cached_tokens": cached_text + cached_audio,
            "cached_tokens_details": {
                "text_tokens": cached_text,
                "audio_tokens": cached_audio,
            },
        },
        "output_token_details": {
            "text_tokens": output_text,
            "audio_tokens": output_audio,
        },
    }


def test_cost_uses_per_modality_rates() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage(usage_event(input_text=1000, input_audio=2000, output_audio=3000))

    expected = (
        1000 * RATES.text_input + 2000 * RATES.audio_input + 3000 * RATES.audio_output
    ) / 1_000_000
    assert tracker.cost_usd == expected


def test_cached_tokens_are_billed_at_the_cached_rate() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage(usage_event(input_audio=1000, cached_audio=400))

    expected = (600 * RATES.audio_input + 400 * RATES.audio_cached_input) / 1_000_000
    assert tracker.cost_usd == expected


def test_flat_cached_tokens_fallback_is_attributed_to_audio() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage(
        {
            "input_token_details": {"text_tokens": 0, "audio_tokens": 1000, "cached_tokens": 250},
            "output_token_details": {},
        }
    )

    assert tracker.input.cached_audio_tokens == 250
    expected = (750 * RATES.audio_input + 250 * RATES.audio_cached_input) / 1_000_000
    assert tracker.cost_usd == expected


def test_usage_accumulates_across_responses() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage(usage_event(input_audio=500, output_audio=800))
    tracker.add_usage(usage_event(input_audio=700, output_audio=200))

    assert tracker.response_count == 2
    assert tracker.input.audio_tokens == 1200
    assert tracker.output.audio_tokens == 1000


def test_unknown_model_falls_back_to_mini_rates_and_flags_it() -> None:
    tracker = CostTracker("gpt-realtime-future")
    tracker.add_usage(usage_event(output_audio=1_000_000))

    assert tracker.cost_usd == RATES.audio_output
    assert tracker.snapshot()["rates_known"] is False


def test_malformed_usage_is_ignored() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage({"input_token_details": None, "output_token_details": None})
    tracker.add_usage({"input_token_details": {"audio_tokens": None}})

    assert tracker.cost_usd == 0.0


def test_snapshot_is_json_serializable_and_rounded() -> None:
    tracker = CostTracker(MODEL)
    tracker.add_usage(usage_event(input_audio=1234, output_audio=5678))
    snapshot = tracker.snapshot()

    assert snapshot["model"] == MODEL
    assert snapshot["input"]["audio_tokens"] == 1234
    assert snapshot["output"]["audio_tokens"] == 5678
    assert snapshot["cost_usd"] == round(tracker.cost_usd, 6)
