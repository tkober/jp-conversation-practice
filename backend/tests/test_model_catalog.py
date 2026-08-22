"""Model dropdown contents: curation, live merge and the filters that matter."""

from __future__ import annotations

import httpx
import pytest

from app import model_catalog
from app.model_catalog import (
    SLOTS_BY_KEY,
    ModelCatalog,
    ModelListError,
    is_valid_model_id,
    price_hint,
)

# A slice of a real /v1/models response, kept small but including the ids that
# make the filters non-trivial.
LIVE_PAYLOAD = {
    "data": [
        {"id": "gpt-realtime", "shutdown_date": "2027-01-20"},
        {"id": "gpt-realtime-2.1", "shutdown_date": None},
        {"id": "gpt-realtime-2025-08-28", "shutdown_date": None},
        {"id": "gpt-realtime-whisper", "shutdown_date": None},
        {"id": "gpt-realtime-translate", "shutdown_date": None},
        {"id": "gpt-4o-transcribe-diarize", "shutdown_date": None},
        {"id": "gpt-transcribe", "shutdown_date": None},
        {"id": "gpt-5-codex", "shutdown_date": None},
        {"id": "gpt-5.1", "shutdown_date": None},
        {"id": "text-embedding-3-small", "shutdown_date": None},
        {"id": "../../etc/passwd", "shutdown_date": None},
    ]
}


def option_ids(result, key: str) -> list[str]:
    slot = next(entry for entry in result.slots if entry["key"] == key)
    return [option["id"] for option in slot["options"]]


def find_option(result, key: str, model_id: str) -> dict:
    slot = next(entry for entry in result.slots if entry["key"] == key)
    return next(option for option in slot["options"] if option["id"] == model_id)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    """Serve LIVE_PAYLOAD instead of calling OpenAI."""

    async def fake_fetch(api_base: str, api_key: str) -> dict[str, str | None]:
        return {
            entry["id"]: entry["shutdown_date"]
            for entry in LIVE_PAYLOAD["data"]
            if is_valid_model_id(entry["id"])
        }

    monkeypatch.setattr(model_catalog, "_fetch_models", fake_fetch)


async def build(api_key: str = "sk-test") -> object:
    return await ModelCatalog().build("https://api.openai.com/v1", api_key)


async def test_curated_entries_come_first_and_keep_their_order(live) -> None:
    result = await build()
    ids = option_ids(result, "realtime_model")
    curated = [entry.id for entry in SLOTS_BY_KEY["realtime_model"].curated]

    assert ids[: len(curated)] == curated


async def test_live_extras_are_appended_without_duplicating_curated_ones(live) -> None:
    result = await build()
    ids = option_ids(result, "realtime_model")

    # gpt-realtime is curated *and* in the live payload; it must appear once.
    assert ids.count("gpt-realtime") == 1
    assert "gpt-realtime-2.1" in ids


async def test_realtime_slot_excludes_models_that_only_look_realtime(live) -> None:
    ids = option_ids(await build(), "realtime_model")

    # Both match "realtime" by name but neither holds a conversation.
    assert "gpt-realtime-whisper" not in ids
    assert "gpt-realtime-translate" not in ids


async def test_dated_snapshots_are_left_out_of_the_live_list(live) -> None:
    ids = option_ids(await build(), "realtime_model")

    assert "gpt-realtime-2025-08-28" not in ids


async def test_slots_do_not_bleed_into_each_other(live) -> None:
    result = await build()

    assert "text-embedding-3-small" not in option_ids(result, "analysis_model")
    # A code model is not what writes the German feedback.
    assert "gpt-5-codex" not in option_ids(result, "analysis_model")
    assert "gpt-5.1" in option_ids(result, "analysis_model")
    # Diarisation splits speakers; the realtime input stream is one.
    assert "gpt-4o-transcribe-diarize" not in option_ids(result, "transcription_model")
    assert "gpt-transcribe" in option_ids(result, "transcription_model")


async def test_price_is_shown_only_where_cost_is_actually_tracked(live) -> None:
    result = await build()

    billed = find_option(result, "realtime_model", "gpt-realtime")
    assert billed["rates_known"] is True
    assert "$32" in billed["price_hint"]

    # The analysis model is not billed by CostTracker, so a price would lie.
    untracked = find_option(result, "analysis_model", "gpt-4o-mini")
    assert untracked["price_hint"] is None
    assert untracked["rates_known"] is None


async def test_a_realtime_model_without_rates_is_flagged(live) -> None:
    option = find_option(await build(), "realtime_model", "gpt-realtime-2.1")

    assert option["rates_known"] is False
    assert option["price_hint"] is None


async def test_shutdown_dates_survive_into_the_options(live) -> None:
    assert find_option(await build(), "realtime_model", "gpt-realtime")["shutdown_date"] == (
        "2027-01-20"
    )


async def test_missing_key_degrades_to_the_curated_list(live) -> None:
    result = await build(api_key="")

    assert result.live_ok is False
    assert result.live_detail
    assert option_ids(result, "realtime_model") == [
        entry.id for entry in SLOTS_BY_KEY["realtime_model"].curated
    ]


async def test_an_unreachable_api_degrades_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(api_base: str, api_key: str) -> dict[str, str | None]:
        raise ModelListError("nope")

    monkeypatch.setattr(model_catalog, "_fetch_models", boom)

    result = await build()

    assert result.live_ok is False
    assert result.live_detail == "nope"
    assert option_ids(result, "realtime_model")


async def test_the_live_list_is_fetched_once_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def counting_fetch(api_base: str, api_key: str) -> dict[str, str | None]:
        calls.append(api_key)
        return {"gpt-realtime-2.1": None}

    monkeypatch.setattr(model_catalog, "_fetch_models", counting_fetch)
    catalog = ModelCatalog()

    await catalog.build("https://api.openai.com/v1", "sk-one")
    await catalog.build("https://api.openai.com/v1", "sk-one")
    # A new key belongs to a different account and may see different models.
    await catalog.build("https://api.openai.com/v1", "sk-two")

    assert calls == ["sk-one", "sk-two"]


async def test_malformed_ids_never_reach_the_options() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=LIVE_PAYLOAD))

    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://api.openai.com/v1/models")

    ids = [entry["id"] for entry in response.json()["data"]]
    assert "../../etc/passwd" in ids
    assert not is_valid_model_id("../../etc/passwd")


@pytest.mark.parametrize(
    "model_id, valid",
    [
        ("gpt-realtime-2.1-mini", True),
        ("tts-1-hd", True),
        ("whisper-1", True),
        ("../../etc/passwd", False),
        ("gpt/../x", False),
        ("", False),
        ("-leading-dash", False),
        ("a" * 65, False),
    ],
)
def test_model_id_validation(model_id: str, valid: bool) -> None:
    assert is_valid_model_id(model_id) is valid


def test_price_hint_is_absent_for_models_without_rates() -> None:
    assert price_hint("gpt-realtime") is not None
    assert price_hint("gpt-realtime-2.1") is None
