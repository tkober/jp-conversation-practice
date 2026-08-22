"""Settings API: overrides, fallbacks and secret handling."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app import model_catalog
from app.api import settings as settings_api
from app.config import get_settings


async def read(api: AsyncClient) -> dict:
    return (await api.get("/api/settings")).json()


async def patch(api: AsyncClient, **fields: object) -> dict:
    return (await api.put("/api/settings", json=fields)).json()


async def test_defaults_come_from_the_environment(api: AsyncClient) -> None:
    body = await read(api)
    env = get_settings()

    assert body["realtime_model"] == env.realtime_model
    assert body["analysis_model"] == env.analysis_model
    assert body["scenario_assistant_model"] == env.scenario_assistant_model


async def test_patch_overrides_only_the_given_fields(api: AsyncClient) -> None:
    before = await read(api)

    body = await patch(api, realtime_model="gpt-realtime")

    assert body["realtime_model"] == "gpt-realtime"
    assert body["analysis_model"] == before["analysis_model"]


async def test_clearing_an_override_falls_back_to_the_environment(api: AsyncClient) -> None:
    await patch(api, analysis_model="gpt-4o")
    assert (await read(api))["analysis_model"] == "gpt-4o"

    await patch(api, analysis_model="")

    assert (await read(api))["analysis_model"] == get_settings().analysis_model


async def test_api_key_is_never_returned_in_full(api: AsyncClient) -> None:
    secret = "sk-proj-abcdefghijklmnop1234"
    await patch(api, openai_api_key=secret)

    response = await api.get("/api/settings")
    body = response.json()

    assert body["openai_api_key_set"] is True
    assert body["openai_api_key_hint"] == "…1234"
    assert body["openai_api_key_from_env"] is False
    assert secret not in response.text


async def test_key_source_is_reported_so_the_ui_can_explain_it(api: AsyncClient) -> None:
    # conftest sets an environment key; with no override it must say so, since
    # a key from the environment cannot be cleared from the UI.
    body = await read(api)

    assert body["openai_api_key_set"] is True
    assert body["openai_api_key_from_env"] is True


async def test_unknown_voice_is_rejected(api: AsyncClient) -> None:
    body = await patch(api, realtime_voice="../../etc/passwd")

    assert body["realtime_voice"] == get_settings().realtime_voice


async def test_speed_is_clamped_into_the_supported_range(api: AsyncClient) -> None:
    body = await patch(api, realtime_speed=99)

    assert body["realtime_speed"] == body["speed_max"]


async def test_eagerness_override_and_fallback(api: AsyncClient) -> None:
    assert (await patch(api, realtime_vad_eagerness="high"))["realtime_vad_eagerness"] == "high"

    # Clearing it hands the setting back to the environment.
    body = await patch(api, realtime_vad_eagerness="")

    assert body["realtime_vad_eagerness"] == get_settings().realtime_vad_eagerness


async def test_unknown_eagerness_is_rejected(api: AsyncClient) -> None:
    body = await patch(api, realtime_vad_eagerness="very")

    assert body["realtime_vad_eagerness"] == get_settings().realtime_vad_eagerness


async def test_a_model_name_that_would_escape_the_cache_directory_is_rejected(
    api: AsyncClient,
) -> None:
    # tts_model becomes a directory under .voice-samples/, so the free-text
    # escape hatch in the dropdown must not be able to write outside it.
    body = await patch(api, tts_model="../../etc/passwd")

    assert body["tts_model"] == get_settings().tts_model


async def test_an_unlisted_but_well_formed_model_is_accepted(api: AsyncClient) -> None:
    # The point of the free-text option: a model released after this deploy.
    body = await patch(api, realtime_model="gpt-realtime-9-preview")

    assert body["realtime_model"] == "gpt-realtime-9-preview"


async def test_model_catalog_lists_every_configurable_slot(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_fetch(api_base: str, api_key: str) -> dict[str, str | None]:
        return {"gpt-realtime-2.1": None}

    monkeypatch.setattr(model_catalog, "_fetch_models", fake_fetch)
    monkeypatch.setattr(settings_api, "_catalog", model_catalog.ModelCatalog())

    body = (await api.get("/api/settings/models")).json()

    assert [slot["key"] for slot in body["slots"]] == list(model_catalog.SLOTS_BY_KEY)
    assert body["live_ok"] is True
    realtime = body["slots"][0]
    assert realtime["cost_tracked"] is True
    assert "gpt-realtime-2.1" in [option["id"] for option in realtime["options"]]
