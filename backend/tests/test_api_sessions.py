"""Session history: storing conversations and keeping them stable."""

from __future__ import annotations

from httpx import AsyncClient


def session_payload(**overrides: object) -> dict:
    payload = {
        "scenario_title": "Einkaufen im Kombini",
        "scenario_prompt": "You are the clerk ...",
        "jlpt_level": "N5",
        "model": "gpt-realtime-2.1-mini",
        "voice": "marin",
        "speed": 0.9,
        "instructions": "You are a warm ...",
        "duration_seconds": 94.5,
        "cost_usd": 0.0663,
        "usage": {"cost_usd": 0.0663, "response_count": 3},
        "transcript": [
            {"role": "assistant", "text": "いらっしゃいませ。", "timestamp": 1.0},
            {"role": "user", "text": "こんばんは。", "timestamp": 4.0},
        ],
    }
    payload.update(overrides)
    return payload


async def test_storing_a_session_returns_a_summary(api: AsyncClient) -> None:
    body = (await api.post("/api/sessions", json=session_payload())).json()

    assert body["scenario_title"] == "Einkaufen im Kombini"
    assert body["turn_count"] == 2
    assert body["has_analysis"] is False
    assert body["cost_usd"] == 0.0663


async def test_detail_carries_transcript_and_instructions(api: AsyncClient) -> None:
    created = (await api.post("/api/sessions", json=session_payload())).json()

    body = (await api.get(f"/api/sessions/{created['id']}")).json()

    assert [turn["text"] for turn in body["transcript"]] == ["いらっしゃいませ。", "こんばんは。"]
    assert body["instructions"].startswith("You are a warm")
    assert body["scenario_prompt"] == "You are the clerk ..."


async def test_list_is_newest_first_and_omits_transcripts(api: AsyncClient) -> None:
    await api.post("/api/sessions", json=session_payload(scenario_title="Erste"))
    await api.post("/api/sessions", json=session_payload(scenario_title="Zweite"))

    body = (await api.get("/api/sessions")).json()

    assert [row["scenario_title"] for row in body][:2] == ["Zweite", "Erste"]
    assert "transcript" not in body[0]


async def test_analysis_can_be_attached_afterwards(api: AsyncClient) -> None:
    """The analysis arrives seconds after the session is already stored."""
    created = (await api.post("/api/sessions", json=session_payload())).json()

    summary = (
        await api.put(
            f"/api/sessions/{created['id']}/analysis",
            json={"summary": "Gut gemacht", "grammar_notes": [], "anki_cards": []},
        )
    ).json()

    assert summary["has_analysis"] is True
    detail = (await api.get(f"/api/sessions/{created['id']}")).json()
    assert detail["analysis"]["summary"] == "Gut gemacht"


async def test_editing_the_scenario_does_not_rewrite_history(api: AsyncClient) -> None:
    scenarios = (await api.get("/api/scenarios")).json()
    konbini = next(row for row in scenarios if row["slug"] == "konbini")
    created = (
        await api.post(
            "/api/sessions",
            json=session_payload(scenario_id=konbini["id"], scenario_prompt=konbini["prompt"]),
        )
    ).json()

    await api.put(f"/api/scenarios/{konbini['id']}", json={"prompt": "Completely different."})

    detail = (await api.get(f"/api/sessions/{created['id']}")).json()
    assert detail["scenario_prompt"] == konbini["prompt"]


async def test_deleting_the_scenario_keeps_the_session(api: AsyncClient) -> None:
    scenario = (
        await api.post("/api/scenarios", json={"title": "Kurzlebig", "prompt": "You are brief."})
    ).json()
    created = (
        await api.post("/api/sessions", json=session_payload(scenario_id=scenario["id"]))
    ).json()

    await api.delete(f"/api/scenarios/{scenario['id']}")

    detail = (await api.get(f"/api/sessions/{created['id']}")).json()
    assert detail["id"] == created["id"]
    assert detail["scenario_title"] == "Einkaufen im Kombini"


async def test_stats_sum_cost_and_duration(api: AsyncClient) -> None:
    await api.post("/api/sessions", json=session_payload(cost_usd=0.01, duration_seconds=60))
    await api.post("/api/sessions", json=session_payload(cost_usd=0.02, duration_seconds=30))

    body = (await api.get("/api/sessions/stats")).json()

    assert body["session_count"] == 2
    assert body["total_cost_usd"] == 0.03
    assert body["total_seconds"] == 90


async def test_deleting_a_session(api: AsyncClient) -> None:
    created = (await api.post("/api/sessions", json=session_payload())).json()

    assert (await api.delete(f"/api/sessions/{created['id']}")).status_code == 204
    assert (await api.get(f"/api/sessions/{created['id']}")).status_code == 404
