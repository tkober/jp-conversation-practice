"""WebSocket relay between the Angular frontend and the OpenAI Realtime API.

The browser never sees the API key: it talks to this backend, the backend holds
one upstream WebSocket to OpenAI per session and pumps events in both
directions. On the way through, the relay
  * injects the scenario/level system prompt into ``session.update``,
  * accounts exact token cost from every ``response.done`` event,
  * normalises the various transcript events into a single ``transcript.turn``
    event and annotates it with furigana, and
  * forwards audio deltas as raw binary frames so the browser does not have to
    base64-decode on the hot path.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.asyncio.client import connect as ws_connect

from .furigana import annotate
from .models import TranscriptTurn
from .pricing import CostTracker
from .prompts import DEFAULT_JLPT_LEVEL, JLPT_GUIDANCE, build_realtime_instructions
from .runtime_config import RuntimeConfig
from .turn_detection import normalise_eagerness
from .voices import is_valid_voice

logger = logging.getLogger(__name__)

# Client events the browser is allowed to forward upstream. Anything else --
# `session.update` in particular -- is dropped so the browser cannot overwrite
# the tutor instructions or the audio format.
ALLOWED_CLIENT_EVENTS = frozenset(
    {
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "input_audio_buffer.clear",
        "conversation.item.create",
        "response.create",
        "response.cancel",
    }
)

# Audio chunk events, sent to the browser as binary frames instead of JSON.
AUDIO_DELTA_EVENTS = frozenset({"response.output_audio.delta", "response.audio.delta"})

# Assistant transcript, completed. Both the GA and the beta event name.
ASSISTANT_TRANSCRIPT_EVENTS = frozenset(
    {"response.output_audio_transcript.done", "response.audio_transcript.done"}
)

USER_TRANSCRIPT_EVENT = "conversation.item.input_audio_transcription.completed"

# Chatty streaming events that the browser has no use for. Dropping them keeps
# the relay's own socket quiet.
NOISY_EVENTS = frozenset(
    {
        "response.output_audio_transcript.delta",
        "response.audio_transcript.delta",
        "response.function_call_arguments.delta",
        "response.output_text.delta",
        "response.text.delta",
        "rate_limits.updated",
    }
)


class RealtimeSession:
    """One browser <-> backend <-> OpenAI conversation."""

    def __init__(self, client_ws: WebSocket, settings: RuntimeConfig) -> None:
        self.client_ws = client_ws
        self.settings = settings
        self.model = settings.realtime_model
        self.cost = CostTracker(self.model)
        self.transcript: list[TranscriptTurn] = []
        self.scenario = ""
        self.jlpt_level = DEFAULT_JLPT_LEVEL
        self.voice = settings.realtime_voice
        self.speed = settings.realtime_speed
        self.eagerness = settings.realtime_vad_eagerness
        self.started_at = time.time()
        self._dropped_event_types: set[str] = set()

    # --- helpers ---------------------------------------------------------

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Send one JSON event to the browser, ignoring a closed socket."""
        if self.client_ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self.client_ws.send_text(json.dumps(payload))
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def send_bytes(self, payload: bytes) -> None:
        if self.client_ws.client_state is not WebSocketState.CONNECTED:
            return
        try:
            await self.client_ws.send_bytes(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def send_error(self, message: str, *, fatal: bool = False) -> None:
        await self.send_json({"type": "app.error", "message": message, "fatal": fatal})

    def _record_turn(self, role: str, text: str) -> TranscriptTurn | None:
        text = (text or "").strip()
        if not text:
            return None
        turn = TranscriptTurn(
            role=role,
            text=text,
            timestamp=time.time() - self.started_at,
            ruby=annotate(text),
        )
        self.transcript.append(turn)
        return turn

    # --- session setup ---------------------------------------------------

    async def _await_start_message(self) -> bool:
        """Wait for the browser's ``app.session.start`` handshake message."""
        try:
            raw = await self.client_ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return False

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_error("First message must be JSON app.session.start.", fatal=True)
            return False

        if message.get("type") != "app.session.start":
            await self.send_error("Expected app.session.start as first message.", fatal=True)
            return False

        self.scenario = str(message.get("scenario") or "").strip()
        level = str(message.get("jlpt_level") or DEFAULT_JLPT_LEVEL).upper()
        self.jlpt_level = level if level in JLPT_GUIDANCE else DEFAULT_JLPT_LEVEL

        requested_voice = str(message.get("voice") or "").strip()
        if is_valid_voice(requested_voice):
            self.voice = requested_voice
        self.speed = self._clamp_speed(message.get("speed"))
        return True

    def _clamp_speed(self, value: Any) -> float:
        """Coerce a requested speed into the supported range."""
        try:
            speed = float(value)
        except (TypeError, ValueError):
            return self.settings.realtime_speed
        return max(
            self.settings.realtime_speed_min,
            min(self.settings.realtime_speed_max, speed),
        )

    def _turn_detection(self) -> dict[str, Any]:
        """How the API decides the learner has finished speaking.

        Sent as a whole block, both at setup and on a live change: a
        ``session.update`` carrying a partial ``turn_detection`` would drop the
        flags it does not mention, and losing ``interrupt_response`` would
        quietly break barge-in.
        """
        return {
            "type": "semantic_vad",
            "eagerness": self.eagerness,
            "create_response": True,
            "interrupt_response": True,
        }

    def _session_update_payload(self) -> dict[str, Any]:
        """Build the ``session.update`` that configures the realtime session."""
        rate = self.settings.audio_sample_rate
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": build_realtime_instructions(self.scenario, self.jlpt_level),
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": rate},
                        "transcription": {
                            "model": self.settings.transcription_model,
                            "language": "ja",
                        },
                        "turn_detection": self._turn_detection(),
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": rate},
                        "voice": self.voice,
                        "speed": self.speed,
                    },
                },
            },
        }

    def _upstream_url(self) -> str:
        return f"{self.settings.openai_realtime_url}?{urlencode({'model': self.model})}"

    def _upstream_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        if self.settings.realtime_beta_header:
            headers["OpenAI-Beta"] = "realtime=v1"
        return headers

    # --- pumps -----------------------------------------------------------

    async def _pump_client_to_openai(self, upstream: Any) -> None:
        """Forward browser messages upstream until the browser disconnects."""
        while True:
            message = await self.client_ws.receive()

            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            if (payload := message.get("bytes")) is not None:
                # Raw PCM16 frame from the microphone worklet.
                await upstream.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(payload).decode("ascii"),
                        }
                    )
                )
                continue

            text = message.get("text")
            if text is None:
                continue

            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                await self.send_error("Received malformed JSON from client.")
                continue

            event_type = event.get("type", "")

            if event_type == "app.session.stop":
                return

            if event_type == "app.session.speed":
                # Translated here rather than allow-listing `session.update`:
                # the browser may change the speed and nothing else.
                self.speed = self._clamp_speed(event.get("speed"))
                await upstream.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "realtime",
                                "audio": {"output": {"speed": self.speed}},
                            },
                        }
                    )
                )
                await self.send_json({"type": "app.speed.changed", "speed": self.speed})
                continue

            if event_type == "app.session.eagerness":
                # Same deal as the speed: a narrow, validated translation into
                # `session.update`, never the raw event from the browser.
                self.eagerness = normalise_eagerness(
                    event.get("eagerness"), self.settings.realtime_vad_eagerness
                )
                await upstream.send(
                    json.dumps(
                        {
                            "type": "session.update",
                            "session": {
                                "type": "realtime",
                                "audio": {"input": {"turn_detection": self._turn_detection()}},
                            },
                        }
                    )
                )
                await self.send_json(
                    {"type": "app.eagerness.changed", "eagerness": self.eagerness}
                )
                continue

            if event_type not in ALLOWED_CLIENT_EVENTS:
                if event_type not in self._dropped_event_types:
                    self._dropped_event_types.add(event_type)
                    logger.warning("Dropped disallowed client event: %s", event_type)
                continue

            await upstream.send(json.dumps(event))

    async def _pump_openai_to_client(self, upstream: Any) -> None:
        """Forward upstream events to the browser, accounting cost on the way."""
        async for raw in upstream:
            if isinstance(raw, bytes):
                # The Realtime API speaks JSON only; ignore anything else.
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ignored malformed upstream message")
                continue

            event_type = event.get("type", "")

            if event_type in AUDIO_DELTA_EVENTS:
                await self._forward_audio_delta(event)
                continue

            if event_type in NOISY_EVENTS:
                continue

            if event_type == "response.done":
                await self._handle_response_done(event)
            elif event_type == USER_TRANSCRIPT_EVENT:
                await self._emit_turn("user", event.get("transcript", ""))
            elif event_type in ASSISTANT_TRANSCRIPT_EVENTS:
                await self._emit_turn("assistant", event.get("transcript", ""))
            elif event_type == "error":
                error = event.get("error") or {}
                logger.error("Realtime API error: %s", error)

            await self.send_json(event)

    async def _forward_audio_delta(self, event: dict[str, Any]) -> None:
        delta = event.get("delta")
        if not isinstance(delta, str):
            return
        try:
            await self.send_bytes(base64.b64decode(delta))
        except (binascii.Error, ValueError):
            logger.warning("Ignored undecodable audio delta")

    async def _emit_turn(self, role: str, text: str) -> None:
        turn = self._record_turn(role, text)
        if turn is None:
            return
        await self.send_json({"type": "app.transcript.turn", "turn": turn.model_dump()})

    async def _handle_response_done(self, event: dict[str, Any]) -> None:
        """Extract the exact usage object and push updated costs downstream."""
        usage = (event.get("response") or {}).get("usage")
        if not usage:
            return
        self.cost.add_usage(usage)
        await self.send_json(
            {
                "type": "app.cost.update",
                "usage": self.cost.snapshot(),
                "elapsed_seconds": round(time.time() - self.started_at, 1),
            }
        )

    # --- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        """Accept the browser socket and relay one full session."""
        await self.client_ws.accept()

        if not self.settings.openai_api_key:
            await self.send_error("OPENAI_API_KEY is not configured on the server.", fatal=True)
            await self.client_ws.close(code=1011)
            return

        if not await self._await_start_message():
            await self.client_ws.close(code=1008)
            return

        try:
            async with ws_connect(
                self._upstream_url(),
                additional_headers=self._upstream_headers(),
                max_size=self.settings.realtime_max_frame_bytes,
                ping_interval=20,
                ping_timeout=20,
            ) as upstream:
                session_update = self._session_update_payload()
                await upstream.send(json.dumps(session_update))
                await self.send_json(
                    {
                        "type": "app.session.started",
                        "model": self.model,
                        "scenario": self.scenario,
                        "jlpt_level": self.jlpt_level,
                        "sample_rate": self.settings.audio_sample_rate,
                        "voice": self.voice,
                        "speed": self.speed,
                        "vad_eagerness": self.eagerness,
                        # Echoed back so a session export can show exactly what
                        # the tutor was told -- without it, debugging an odd
                        # conversation means guessing at the prompt.
                        "instructions": session_update["session"]["instructions"],
                    }
                )
                await self._relay(upstream)
        except websockets.InvalidStatus as exc:
            logger.error("OpenAI rejected the realtime connection: %s", exc)
            await self.send_error(
                f"OpenAI rejected the realtime connection (HTTP {exc.response.status_code}). "
                "Check OPENAI_API_KEY and model access.",
                fatal=True,
            )
        except OSError as exc:
            logger.error("Could not reach the OpenAI Realtime API: %s", exc)
            await self.send_error(f"Could not reach the OpenAI Realtime API: {exc}", fatal=True)
        finally:
            await self._finish()

    async def _relay(self, upstream: Any) -> None:
        """Run both pumps until either side ends the session."""
        tasks = [
            asyncio.create_task(self._pump_client_to_openai(upstream), name="client->openai"),
            asyncio.create_task(self._pump_openai_to_client(upstream), name="openai->client"),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            exc = task.exception()
            if exc is None or isinstance(exc, WebSocketDisconnect):
                continue
            if isinstance(exc, websockets.ConnectionClosed):
                logger.info("Upstream realtime connection closed: %s", exc)
                continue
            raise exc

    async def _finish(self) -> None:
        """Send the closing summary and shut the browser socket down."""
        await self.send_json(
            {
                "type": "app.session.ended",
                "usage": self.cost.snapshot(),
                "elapsed_seconds": round(time.time() - self.started_at, 1),
                "transcript": [turn.model_dump() for turn in self.transcript],
            }
        )
        if self.client_ws.client_state is WebSocketState.CONNECTED:
            try:
                await self.client_ws.close()
            except RuntimeError:
                pass
