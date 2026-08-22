"""Integration test for the realtime relay against a fake OpenAI upstream.

A real ``websockets`` server stands in for the Realtime API so the relay's
actual socket handling, event filtering and cost accounting are exercised.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app import realtime
from app.api import practice
from app.config import get_settings
from app.pricing import MODEL_RATES
from app.prompts import HELP_STAGES, MAX_HELP_STAGE
from app.realtime import USER_TRANSCRIPT_EVENT
from app.runtime_config import build_runtime_config


class FakeRealtimeServer:
    """Minimal stand-in for the OpenAI Realtime API, run on its own loop."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._connected = threading.Event()
        self._message_arrived = threading.Event()

    # --- lifecycle ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=10), "fake realtime server did not start"

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())
        self._loop.run_forever()

    async def _serve(self) -> None:
        server = await serve(self._handler, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()

    async def _handler(self, connection: Any) -> None:
        self._connection = connection
        self.headers = dict(connection.request.headers)
        self._connected.set()
        async for raw in connection:
            self.received.append(json.loads(raw))
            self._message_arrived.set()

    # --- test helpers ---

    def wait_for_connection(self, timeout: float = 5.0) -> None:
        assert self._connected.wait(timeout=timeout), "relay never connected upstream"

    def wait_for_messages(self, count: int, timeout: float = 5.0) -> None:
        deadline = timeout
        step = 0.05
        while len(self.received) < count and deadline > 0:
            self._message_arrived.wait(step)
            self._message_arrived.clear()
            deadline -= step
        assert len(self.received) >= count, f"expected {count} upstream messages, got {self.received}"

    def send_event(self, event: dict[str, Any]) -> None:
        assert self._loop is not None and self._connection is not None
        future = asyncio.run_coroutine_threadsafe(
            self._connection.send(json.dumps(event)), self._loop
        )
        future.result(timeout=5)


@pytest.fixture
def upstream() -> Any:
    server = FakeRealtimeServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def config(upstream: FakeRealtimeServer, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point the relay at the fake upstream instead of api.openai.com."""
    env = get_settings()
    monkeypatch.setattr(env, "openai_api_key", "test-key")
    monkeypatch.setattr(env, "openai_realtime_url", f"ws://127.0.0.1:{upstream.port}")
    return build_runtime_config(None, env)


@pytest.fixture
def client(config: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    async def fixed_config() -> Any:
        return config

    monkeypatch.setattr(practice, "session_config", fixed_config)
    with TestClient(practice_app()) as test_client:
        yield test_client


def practice_app() -> Any:
    """A bare app carrying only the relay route, so no database is needed."""
    from fastapi import FastAPI

    app = FastAPI()
    app.add_api_websocket_route("/ws/realtime", practice.realtime_endpoint)
    return app


def start_session(
    websocket: Any, upstream: FakeRealtimeServer, **extra: Any
) -> dict[str, Any]:
    websocket.send_text(
        json.dumps(
            {
                "type": "app.session.start",
                "scenario": "Kombini",
                "jlpt_level": "N4",
                **extra,
            }
        )
    )
    upstream.wait_for_connection()
    upstream.wait_for_messages(1)
    return websocket.receive_json()


# Stand-ins for `scenario_attachments` rows. The relay only ever reads the
# four fields `to_context_item` maps, and the query itself is covered against a
# real database in test_api_attachments; going through one here would mean
# using the shared engine from TestClient's worker thread, which it cannot be.
MATERIAL = {
    7: SimpleNamespace(
        id=7, kind="image", title="Speisekarte", description="唐揚げ (からあげ) – 600円"
    ),
    8: SimpleNamespace(
        id=8, kind="image", title="Kartenausschnitt", description="A map of the station area."
    ),
}


@pytest.fixture(autouse=True)
def material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the fake material without touching a database."""

    class _Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

    async def load(_session: Any, _scenario_id: int, ids: set[int] | None = None) -> list[Any]:
        return [MATERIAL[key] for key in sorted(ids or ()) if key in MATERIAL]

    monkeypatch.setattr(realtime, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(realtime, "load_scenario_attachments", load)


def responses(upstream: FakeRealtimeServer) -> list[dict[str, Any]]:
    """Only the response.create messages, in order.

    A help press also sends the slowdown, so counting raw messages would tie
    every assertion below to that ordering.
    """
    return [message for message in upstream.received if message.get("type") == "response.create"]


def test_session_update_carries_scenario_and_audio_format(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream)

        assert started["type"] == "app.session.started"
        assert started["jlpt_level"] == "N4"

        session_update = upstream.received[0]
        assert session_update["type"] == "session.update"

        session = session_update["session"]
        assert "Kombini" in session["instructions"]
        assert "JLPT N4" in session["instructions"]
        assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
        assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"


def test_session_started_echoes_the_instructions(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream)

        # The export feature relies on this: a transcript alone does not explain
        # why a conversation went wrong, the prompt usually does.
        assert started["instructions"] == upstream.received[0]["session"]["instructions"]
        assert "Kombini" in started["instructions"]


def test_voice_and_speed_from_the_handshake_reach_the_session(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "type": "app.session.start",
                    "scenario": "Kombini",
                    "jlpt_level": "N5",
                    "voice": "cedar",
                    "speed": 0.8,
                }
            )
        )
        upstream.wait_for_connection()
        upstream.wait_for_messages(1)
        started = websocket.receive_json()

        output = upstream.received[0]["session"]["audio"]["output"]
        assert output["voice"] == "cedar"
        assert output["speed"] == 0.8
        assert started["voice"] == "cedar"
        assert started["speed"] == 0.8


def test_unknown_voice_falls_back_to_the_configured_default(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        websocket.send_text(
            json.dumps(
                {"type": "app.session.start", "scenario": "x", "voice": "../../etc/passwd"}
            )
        )
        upstream.wait_for_connection()
        upstream.wait_for_messages(1)
        websocket.receive_json()

        assert (
            upstream.received[0]["session"]["audio"]["output"]["voice"]
            == config.realtime_voice
        )


def test_speed_is_clamped_to_the_supported_range(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        websocket.send_text(
            json.dumps({"type": "app.session.start", "scenario": "x", "speed": 99})
        )
        upstream.wait_for_connection()
        upstream.wait_for_messages(1)
        websocket.receive_json()

        assert (
            upstream.received[0]["session"]["audio"]["output"]["speed"]
            == config.realtime_speed_max
        )


def test_live_speed_change_sends_a_narrow_session_update(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.speed", "speed": 1.25}))
        upstream.wait_for_messages(2)

        update = upstream.received[1]
        assert update["type"] == "session.update"
        # Only the speed may travel this path -- never instructions or voice.
        assert update["session"]["audio"] == {"output": {"speed": 1.25}}
        assert "instructions" not in update["session"]

        assert websocket.receive_json() == {"type": "app.speed.changed", "speed": 1.25}


def test_configured_eagerness_reaches_the_turn_detection(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream)

        turn_detection = upstream.received[0]["session"]["audio"]["input"]["turn_detection"]
        assert turn_detection["eagerness"] == config.realtime_vad_eagerness
        assert started["vad_eagerness"] == config.realtime_vad_eagerness


def test_live_eagerness_change_resends_the_whole_turn_detection(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.eagerness", "eagerness": "high"}))
        upstream.wait_for_messages(2)

        update = upstream.received[1]
        assert update["type"] == "session.update"
        # The whole block, not just the changed field: a partial turn_detection
        # would drop interrupt_response and silently break barge-in.
        assert update["session"]["audio"] == {
            "input": {
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "high",
                    "create_response": True,
                    "interrupt_response": True,
                }
            }
        }
        assert "instructions" not in update["session"]

        assert websocket.receive_json() == {
            "type": "app.eagerness.changed",
            "eagerness": "high",
        }


def test_unknown_eagerness_falls_back_to_the_configured_default(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.eagerness", "eagerness": "yes"}))
        upstream.wait_for_messages(2)

        turn_detection = upstream.received[1]["session"]["audio"]["input"]["turn_detection"]
        assert turn_detection["eagerness"] == config.realtime_vad_eagerness


def test_upstream_receives_the_api_key(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        assert upstream.headers["authorization"] == "Bearer test-key"
        # GA realtime models must not receive the beta header.
        assert "openai-beta" not in upstream.headers


def test_binary_microphone_frames_become_base64_appends(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        pcm = b"\x01\x02\x03\x04"
        websocket.send_bytes(pcm)
        upstream.wait_for_messages(2)

        append = upstream.received[1]
        assert append["type"] == "input_audio_buffer.append"
        assert base64.b64decode(append["audio"]) == pcm


def test_disallowed_client_events_are_not_forwarded(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        # The browser must not be able to rewrite the tutor instructions.
        websocket.send_text(
            json.dumps({"type": "session.update", "session": {"instructions": "hacked"}})
        )
        websocket.send_text(json.dumps({"type": "input_audio_buffer.commit"}))
        upstream.wait_for_messages(2)

        assert [message["type"] for message in upstream.received] == [
            "session.update",
            "input_audio_buffer.commit",
        ]


def test_wakaranai_asks_for_one_scaffolded_response(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)

        assert websocket.receive_json() == {
            "type": "app.help.stage",
            "stage": 1,
            "max_stage": MAX_HELP_STAGE,
        }

        instructions = responses(upstream)[0]["response"]["instructions"]
        # The whole session frame is rebuilt, because `response.instructions`
        # replaces the session prompt rather than adding to it.
        assert "Kombini" in instructions
        assert "JLPT N4" in instructions
        assert HELP_STAGES[0] in instructions
        assert f"help attempt 1 of {MAX_HELP_STAGE}" in instructions


def test_pressing_again_escalates_and_speaking_resets_it(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(5)
        assert websocket.receive_json()["stage"] == 2
        assert HELP_STAGES[1] in responses(upstream)[1]["response"]["instructions"]

        # Saying something means the learner is past the spot they were stuck
        # in, so the next press starts the escalation over.
        upstream.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "これください",
            }
        )
        assert websocket.receive_json() == {
            "type": "app.help.stage",
            "stage": 0,
            "max_stage": MAX_HELP_STAGE,
        }
        websocket.receive_json()  # app.transcript.turn
        websocket.receive_json()  # the relayed raw event

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(7)
        assert websocket.receive_json()["stage"] == 1


def test_noise_that_transcribes_to_nothing_does_not_reset_the_escalation(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    """The VAD commits background noise as a turn; it transcribes to nothing.

    Resetting on one of those left a learner who sat silent and pressed the
    button over and over stuck on stage 1 -- and invisibly so, because an empty
    turn never reaches the transcript.
    """
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        assert websocket.receive_json()["stage"] == 1

        upstream.send_event(
            {"type": USER_TRANSCRIPT_EVENT, "transcript": "   "}
        )
        websocket.receive_json()  # the relayed raw event, and nothing else

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(5)
        assert websocket.receive_json()["stage"] == 2


def test_a_help_turn_is_marked_in_the_transcript(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    """An export cannot otherwise tell help apart from an ordinary reply."""
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        upstream.send_event(
            {"type": "response.output_audio_transcript.done", "transcript": "何にしますか"}
        )
        assert websocket.receive_json()["turn"]["help_stage"] is None
        websocket.receive_json()  # the relayed raw event
        upstream.send_event({"type": "response.done", "response": {}})
        websocket.receive_json()  # the relayed raw event

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()  # app.help.stage

        upstream.send_event(
            {"type": "response.output_audio_transcript.done", "transcript": "飲み物ですか"}
        )
        assert websocket.receive_json()["turn"]["help_stage"] == 1

        websocket.receive_json()  # the relayed raw event
        upstream.send_event({"type": "response.done", "response": {}})
        websocket.receive_json()  # the relayed raw event

        # The marker stops with the response it belonged to.
        upstream.send_event(
            {"type": "response.output_audio_transcript.done", "transcript": "はい"}
        )
        assert websocket.receive_json()["turn"]["help_stage"] is None


def test_the_last_stage_is_german_and_does_not_run_past_it(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        for _ in range(MAX_HELP_STAGE + 2):
            websocket.send_text(json.dumps({"type": "app.session.help"}))

        presses = MAX_HELP_STAGE + 2
        # One slowdown plus one response.create per press, after the setup.
        upstream.wait_for_messages(1 + 2 * presses)

        stages = [websocket.receive_json()["stage"] for _ in range(presses)]
        assert stages == list(range(1, MAX_HELP_STAGE + 1)) + [MAX_HELP_STAGE] * 2

        last = responses(upstream)[-1]["response"]["instructions"]
        assert HELP_STAGES[-1] in last
        # The escalation ends in German -- that is the point of the last stage.
        assert "German" in HELP_STAGES[-1]
        # And it has to say so loudly enough to beat the "speak ONLY Japanese"
        # rule sitting above it in the same prompt, which it otherwise loses to.
        assert "OVERRIDES" in HELP_STAGES[-1]


def test_wakaranai_slows_the_tutor_down_and_puts_the_speed_back(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream)
        assert started["help_speed_factor"] == config.realtime_help_speed_factor

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()  # app.help.stage

        # There is no per-response speed in the Realtime API, so the slowdown
        # goes through session.update -- carrying the speed and nothing else.
        slow_down = upstream.received[1]
        assert slow_down["type"] == "session.update"
        expected = config.realtime_speed * config.realtime_help_speed_factor
        assert slow_down["session"]["audio"] == {"output": {"speed": expected}}
        assert "instructions" not in slow_down["session"]
        assert upstream.received[2]["type"] == "response.create"

        upstream.send_event({"type": "response.done", "response": {}})
        upstream.wait_for_messages(4)

        # ... and the next ordinary turn is back at the learner's own rate.
        assert upstream.received[3]["session"]["audio"] == {
            "output": {"speed": config.realtime_speed}
        }


def test_the_help_rate_never_drops_below_the_slider_floor(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    """At the slowest setting there is nothing left to give."""
    with client.websocket_connect("/ws/realtime") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "type": "app.session.start",
                    "scenario": "x",
                    "speed": config.realtime_speed_min,
                }
            )
        )
        upstream.wait_for_connection()
        upstream.wait_for_messages(1)
        websocket.receive_json()

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()

        assert upstream.received[1]["session"]["audio"] == {
            "output": {"speed": config.realtime_speed_min}
        }


def test_a_slider_move_during_a_help_turn_lands_when_it_ends(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    """The help turn keeps its rate; the new one applies from the next reply."""
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()

        websocket.send_text(json.dumps({"type": "app.session.speed", "speed": 1.2}))
        assert websocket.receive_json() == {"type": "app.speed.changed", "speed": 1.2}

        upstream.send_event({"type": "response.done", "response": {}})
        upstream.wait_for_messages(4)

        # One update, not two: the slider did not interrupt the help turn.
        assert upstream.received[3]["session"]["audio"] == {"output": {"speed": 1.2}}
        assert len(upstream.received) == 4


def test_the_help_block_demands_a_smaller_turn(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    """The failure this was written for: help that came out longer than the
    sentence the learner had not understood."""
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()

        instructions = upstream.received[2]["response"]["instructions"]
        assert "Fewer words than your last turn" in instructions
        assert "word for word" in instructions
        # Asking the model to speak more slowly only ever produced a verbatim
        # repeat, so no stage may ask for one; the rate is handled for it.
        assert not any("more slowly" in stage for stage in HELP_STAGES)


def test_wakaranai_cancels_a_response_that_is_still_being_generated(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        upstream.send_event({"type": "response.created", "response": {}})
        websocket.receive_json()  # the relayed raw event

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(2)
        websocket.receive_json()  # app.help.stage

        # The API refuses a second response while one is running, so the help
        # request has to wait for the cancellation to land.
        assert upstream.received[1] == {"type": "response.cancel"}

        upstream.send_event(
            {"type": "response.done", "response": {"status": "cancelled"}}
        )
        upstream.wait_for_messages(4)
        assert upstream.received[2]["type"] == "session.update"  # the slowdown
        assert upstream.received[3]["type"] == "response.create"


def test_response_done_produces_a_cost_update(
    client: TestClient, upstream: FakeRealtimeServer, config: Any
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        upstream.send_event(
            {
                "type": "response.done",
                "response": {
                    "usage": {
                        "total_tokens": 3000,
                        "input_token_details": {"text_tokens": 0, "audio_tokens": 1000},
                        "output_token_details": {"text_tokens": 0, "audio_tokens": 2000},
                    }
                },
            }
        )

        cost_update = websocket.receive_json()
        assert cost_update["type"] == "app.cost.update"

        rates = MODEL_RATES[config.realtime_model]
        expected = (1000 * rates.audio_input + 2000 * rates.audio_output) / 1_000_000
        assert cost_update["usage"]["cost_usd"] == round(expected, 6)

        # The raw upstream event is relayed as well, so the UI can react to it.
        assert websocket.receive_json()["type"] == "response.done"


def test_audio_deltas_arrive_as_binary_frames(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        pcm = b"\x10\x20\x30\x40"
        upstream.send_event(
            {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(pcm).decode("ascii"),
            }
        )

        assert websocket.receive_bytes() == pcm


def test_transcripts_are_normalised_into_app_events(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        upstream.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "これください",
            }
        )
        turn = websocket.receive_json()
        assert turn["type"] == "app.transcript.turn"
        assert turn["turn"] == {
            "role": "user",
            "text": "これください",
            "timestamp": pytest.approx(0, abs=10),
            # Kana only, so there is no reading to put anywhere.
            "ruby": None,
            # Nobody pressed わからない, so this is an ordinary turn.
            "help_stage": None,
        }

        websocket.receive_json()  # the relayed raw event

        upstream.send_event(
            {"type": "response.output_audio_transcript.done", "transcript": "はい、お水をどうぞ"}
        )
        assistant_turn = websocket.receive_json()
        assert assistant_turn["turn"]["role"] == "assistant"
        assert assistant_turn["turn"]["text"] == "はい、お水をどうぞ"
        # Furigana rides along with the turn, so the UI needs no second call.
        assert {"text": "水", "reading": "みず"} in assistant_turn["turn"]["ruby"]


def test_session_end_reports_transcript_and_totals(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream)

        upstream.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "おねがいします",
            }
        )
        websocket.receive_json()
        websocket.receive_json()

        websocket.send_text(json.dumps({"type": "app.session.stop"}))

        ended = websocket.receive_json()
        assert ended["type"] == "app.session.ended"
        assert [turn["text"] for turn in ended["transcript"]] == ["おねがいします"]


def test_missing_api_key_is_reported_to_the_client(
    upstream: FakeRealtimeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = get_settings()
    monkeypatch.setattr(env, "openai_api_key", "")

    async def keyless_config() -> Any:
        return build_runtime_config(None, env)

    monkeypatch.setattr(practice, "session_config", keyless_config)

    with TestClient(practice_app()) as test_client:
        with test_client.websocket_connect("/ws/realtime") as websocket:
            error = websocket.receive_json()

    assert error["type"] == "app.error"
    assert error["fatal"] is True
    assert "OPENAI_API_KEY" in error["message"]


def test_context_material_from_the_handshake_reaches_the_prompt(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream, scenario_id=1, context_ids=[7])

        instructions = upstream.received[0]["session"]["instructions"]
        assert "# Context material" in instructions
        assert "唐揚げ (からあげ) – 600円" in instructions
        # Echoed back so the session screen can show the learner the same thing
        # the tutor was told about -- without that, deixis has nothing to point
        # at.
        assert [item["id"] for item in started["context_items"]] == [7]


def test_material_ids_are_looked_up_not_taken_from_the_browser(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(
            websocket,
            upstream,
            scenario_id=1,
            context_ids=[7, 999, "not-an-id"],
        )

        instructions = upstream.received[0]["session"]["instructions"]
        # The browser names which material, never what it says: an id with no
        # row behind it contributes nothing, and a non-id is dropped.
        assert "唐揚げ" in instructions
        assert "not-an-id" not in instructions


def test_material_without_a_scenario_id_is_ignored(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        started = start_session(websocket, upstream, context_ids=[7])

        # A free-text scenario owns no material, so there is nothing to look
        # up -- and nothing to complain about either.
        assert started["type"] == "app.session.started"
        assert started["context_items"] == []
        assert "# Context material" not in upstream.received[0]["session"]["instructions"]


def test_material_that_cannot_be_found_is_reported(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "type": "app.session.start",
                    "scenario": "Kombini",
                    "scenario_id": 1,
                    "context_ids": [999],
                }
            )
        )
        upstream.wait_for_connection()
        upstream.wait_for_messages(1)

        # Ticking a menu on the setup screen and then not having it is worth a
        # line; the session still starts without it.
        error = websocket.receive_json()
        assert error["type"] == "app.error"
        assert error["fatal"] is False
        assert websocket.receive_json()["type"] == "app.session.started"


def test_handing_material_over_mid_session_rebuilds_the_instructions(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream, scenario_id=1, context_ids=[7])

        websocket.send_text(
            json.dumps({"type": "app.session.context", "attachment_id": 8})
        )
        upstream.wait_for_messages(2)

        update = upstream.received[1]
        assert update["type"] == "session.update"
        # Only the instructions travel this path, and they are rebuilt here
        # from the trusted frame rather than sent by the browser.
        assert set(update["session"]) == {"type", "instructions"}
        instructions = update["session"]["instructions"]
        assert "A map of the station area." in instructions
        # The material that was already there stays.
        assert "唐揚げ" in instructions

        added = websocket.receive_json()
        assert added["type"] == "app.context.added"
        assert added["item"]["id"] == 8
        assert added["item"]["introduced_at"] is not None


def test_handing_the_same_material_over_twice_changes_nothing(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream, scenario_id=1, context_ids=[7])

        websocket.send_text(
            json.dumps({"type": "app.session.context", "attachment_id": 7})
        )
        websocket.send_text(json.dumps({"type": "input_audio_buffer.commit"}))
        upstream.wait_for_messages(2)

        # No second session.update: the material is already in the prompt, and
        # re-sending it would only claim it had just been handed over.
        assert [message["type"] for message in upstream.received] == [
            "session.update",
            "input_audio_buffer.commit",
        ]


def test_a_wakaranai_turn_knows_about_the_material(
    client: TestClient, upstream: FakeRealtimeServer
) -> None:
    with client.websocket_connect("/ws/realtime") as websocket:
        start_session(websocket, upstream, scenario_id=1, context_ids=[7])

        websocket.send_text(json.dumps({"type": "app.session.help"}))
        upstream.wait_for_messages(3)
        websocket.receive_json()

        # `response.instructions` replaces the session prompt, so the material
        # has to be rebuilt into it -- pointing at the menu is one of the
        # better ways out of a spot where words are not landing.
        instructions = responses(upstream)[0]["response"]["instructions"]
        assert "唐揚げ (からあげ) – 600円" in instructions
        assert HELP_STAGES[0] in instructions
