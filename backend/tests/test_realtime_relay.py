"""Integration test for the realtime relay against a fake OpenAI upstream.

A real ``websockets`` server stands in for the Realtime API so the relay's
actual socket handling, event filtering and cost accounting are exercised.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app.api import practice
from app.config import get_settings
from app.pricing import MODEL_RATES
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


def start_session(websocket: Any, upstream: FakeRealtimeServer) -> dict[str, Any]:
    websocket.send_text(
        json.dumps(
            {"type": "app.session.start", "scenario": "Kombini", "jlpt_level": "N4"}
        )
    )
    upstream.wait_for_connection()
    upstream.wait_for_messages(1)
    return websocket.receive_json()


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
        }

        websocket.receive_json()  # the relayed raw event

        upstream.send_event(
            {"type": "response.output_audio_transcript.done", "transcript": "はい、どうぞ"}
        )
        assistant_turn = websocket.receive_json()
        assert assistant_turn["turn"]["role"] == "assistant"
        assert assistant_turn["turn"]["text"] == "はい、どうぞ"


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
