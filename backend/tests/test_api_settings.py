"""Settings API: overrides, fallbacks and secret handling."""

from __future__ import annotations

from httpx import AsyncClient

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
