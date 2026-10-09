"""Session history: storing conversations and keeping them stable."""

from __future__ import annotations

from httpx import AsyncClient


def session_payload(**overrides: object) -> dict:
    payload = {
        "scenario_title": "Shopping at the konbini",
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
            {
                "type": "speech",
                "role": "assistant",
                "text": "お会計は千円です。",
                "timestamp": 1.0,
            },
            {"type": "speech", "role": "user", "text": "こんばんは。", "timestamp": 4.0},
        ],
    }
    payload.update(overrides)
    return payload


async def test_storing_a_session_returns_a_summary(api: AsyncClient) -> None:
    body = (await api.post("/api/sessions", json=session_payload())).json()

    assert body["scenario_title"] == "Shopping at the konbini"
    assert body["turn_count"] == 2  # speech only, see below
    assert body["has_analysis"] is False
    assert body["cost_usd"] == 0.0663


async def test_detail_carries_transcript_and_instructions(api: AsyncClient) -> None:
    created = (await api.post("/api/sessions", json=session_payload())).json()

    body = (await api.get(f"/api/sessions/{created['id']}")).json()

    assert [turn["text"] for turn in body["transcript"]] == ["お会計は千円です。", "こんばんは。"]
    # Furigana is added on the way out rather than stored, so a session
    # recorded before the feature existed shows the readings too.
    assert {"text": "会計", "reading": "かいけい"} in body["transcript"][0]["ruby"]
    assert body["instructions"].startswith("You are a warm")
    assert body["scenario_prompt"] == "You are the clerk ..."


async def test_stored_transcripts_stay_plain(api: AsyncClient) -> None:
    """The row keeps the text; the readings are derived on read."""
    payload = session_payload()
    payload["transcript"][0]["ruby"] = [{"text": "お会計", "reading": "でたらめ"}]

    created = (await api.post("/api/sessions", json=payload)).json()
    body = (await api.get(f"/api/sessions/{created['id']}")).json()

    assert {"text": "会計", "reading": "かいけい"} in body["transcript"][0]["ruby"]


async def test_presses_and_handovers_are_stored_alongside_the_speech(
    api: AsyncClient,
) -> None:
    payload = session_payload()
    payload["transcript"] = [
        {"type": "speech", "role": "assistant", "text": "ご注文は？", "timestamp": 1.0},
        {"type": "help", "stage": 1, "max_stage": 4, "timestamp": 3.0},
        {
            "type": "context",
            "timestamp": 5.0,
            "item": {"id": 7, "kind": "image", "title": "Speisekarte", "description": "…"},
        },
        {"type": "speech", "role": "user", "text": "これください。", "timestamp": 9.0},
    ]

    created = (await api.post("/api/sessions", json=payload)).json()
    detail = (await api.get(f"/api/sessions/{created['id']}")).json()

    assert [event["type"] for event in detail["transcript"]] == [
        "speech",
        "help",
        "context",
        "speech",
    ]
    assert detail["transcript"][2]["item"]["title"] == "Speisekarte"
    # A press is not a Redebeitrag: the history header counts what was said.
    assert created["turn_count"] == 2


async def test_a_transcript_stored_before_events_still_reads(api: AsyncClient) -> None:
    """Rows written when the transcript was a plain list of turns.

    Those carry real practice history, so they are upgraded on the way out
    rather than rewritten in place -- the same trade the furigana makes.
    """
    created = (await api.post("/api/sessions", json=session_payload())).json()

    # Put the row back the way the old code would have written it. Only the
    # read path has to cope with this shape -- nothing writes it any more, so
    # POST rejects it, which is why the row is rewritten underneath. Through
    # the ORM rather than raw SQL: the column is JSONB on one backend and JSON
    # on the other, and a bound string only lands on one of them.
    from app import db

    async with db.get_sessionmaker()() as session:
        row = await session.get(db.Session, created["id"])
        row.transcript = [{"role": "user", "text": "こんばんは。"}]
        await session.commit()

    detail = (await api.get(f"/api/sessions/{created['id']}")).json()

    assert detail["transcript"] == [
        {
            "type": "speech",
            "timestamp": None,
            "role": "user",
            "text": "こんばんは。",
            "ruby": None,
            "help_stage": None,
        }
    ]


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
    assert detail["scenario_title"] == "Shopping at the konbini"


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
