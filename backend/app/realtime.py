"""WebSocket relay between the Angular frontend and the OpenAI Realtime API.

The browser never sees the API key: it talks to this backend, the backend holds
one upstream WebSocket to OpenAI per session and pumps events in both
directions. On the way through, the relay
  * injects the scenario/level system prompt into ``session.update``,
  * accounts exact token cost from every ``response.done`` event,
  * normalises the various transcript events into a single ``transcript.turn``
    event and annotates it with furigana,
  * turns a わからない press into one scaffolded response, escalating with
    every press until the learner speaks again,
  * folds the scenario's context material into the prompt, both at the start
    and when the learner is handed something mid-conversation, and
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

from .context_material import to_context_item
from .db import get_sessionmaker, load_scenario_attachments
from .furigana import annotate
from .models import ContextItem, TranscriptTurn
from .pricing import CostTracker
from .prompts import (
    DEFAULT_JLPT_LEVEL,
    JLPT_GUIDANCE,
    MAX_HELP_STAGE,
    build_help_instructions,
    build_realtime_instructions,
)
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


def _as_int(value: Any) -> int | None:
    """A row id from an untrusted message, or None if it is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_ids(value: Any) -> set[int]:
    """The ids in a handshake list, silently dropping anything that is not one."""
    if not isinstance(value, list):
        return set()
    return {number for number in (_as_int(entry) for entry in value) if number is not None}


class RealtimeSession:
    """One browser <-> backend <-> OpenAI conversation."""

    def __init__(self, client_ws: WebSocket, settings: RuntimeConfig) -> None:
        self.client_ws = client_ws
        self.settings = settings
        self.model = settings.realtime_model
        self.cost = CostTracker(self.model)
        self.transcript: list[TranscriptTurn] = []
        self.scenario = ""
        self.scenario_id: int | None = None
        self.jlpt_level = DEFAULT_JLPT_LEVEL
        # Context material the tutor currently knows about, in the order it
        # arrived. Everything here came out of the database, never out of a
        # browser message -- see `_add_context`.
        self.context_items: list[ContextItem] = []
        self.voice = settings.realtime_voice
        self.speed = settings.realtime_speed
        self.eagerness = settings.realtime_vad_eagerness
        self.started_at = time.time()
        self._dropped_event_types: set[str] = set()
        # わからない: how often the learner has asked for help in this same
        # spot. Reset as soon as they manage to say something again.
        self.help_stage = 0
        self._response_active = False
        self._help_pending = False
        self._help_speed_active = False
        # Set while the response to a press is being generated, so the turn it
        # produces can be told apart from an ordinary one afterwards.
        self._help_turn_stage: int | None = None

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
            # Which わからない press this answers, if any. Without it an export
            # cannot tell a help turn from an ordinary one -- which is exactly
            # what you need to know when the help was not helpful.
            help_stage=self._help_turn_stage if role == "assistant" else None,
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

        self.scenario_id = _as_int(message.get("scenario_id"))
        requested = _as_ids(message.get("context_ids"))
        self.context_items = await self._load_context(requested)
        if requested and self.scenario_id is not None and not self.context_items:
            # Ticking a menu on the setup screen and then not having it is
            # confusing enough to be worth a line, whether the cause was the
            # database or a row that is no longer there.
            await self.send_error(
                "The scenario's context material could not be loaded; the "
                "conversation runs without it."
            )
        return True

    async def _load_context(self, ids: set[int]) -> list[ContextItem]:
        """Fetch the chosen material for this session, by id.

        The browser names *which* material, never what it says: the text that
        reaches the prompt is read here, out of the row the id points at. That
        is the same boundary `ALLOWED_CLIENT_EVENTS` draws -- the browser
        chooses, the backend decides what the words are.

        No ids means no database work at all, which is what keeps a session
        without material (and the relay's own tests) off the database entirely.
        """
        if not ids or self.scenario_id is None:
            return []
        try:
            async with get_sessionmaker()() as session:
                rows = await load_scenario_attachments(session, self.scenario_id, ids)
        except Exception:  # noqa: BLE001 - material must not sink the session
            logger.exception(
                "Could not load context material for scenario %s", self.scenario_id
            )
            # Reported by the caller, which knows whether this was the session
            # starting up or the learner handing something over.
            return []
        return [to_context_item(row) for row in rows]

    async def _add_context(self, upstream: Any, attachment_id: int | None) -> None:
        """Hand the learner one more piece of material, mid-conversation.

        The third and last place the browser moves ``session.update``, and it
        obeys the same rule as the speed and the eagerness: the browser sends
        an id, and the payload is rebuilt here from the trusted frame. Sending
        the whole instructions rather than a conversation item is what makes
        the material stick -- a ``response.instructions`` for a わからない turn
        rebuilds the frame from scratch, and would otherwise be the one turn
        that has forgotten the menu the learner is holding.
        """
        if attachment_id is None or self.scenario_id is None:
            return
        if any(item.id == attachment_id for item in self.context_items):
            return

        items = await self._load_context({attachment_id})
        if not items:
            await self.send_error("That context material could not be loaded.")
            return

        item = items[0]
        item.introduced_at = round(time.time() - self.started_at, 1)
        self.context_items.append(item)

        await upstream.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "instructions": self._instructions(),
                    },
                }
            )
        )
        await self.send_json({"type": "app.context.added", "item": item.model_dump()})

    def _instructions(self) -> str:
        """The tutor's system prompt as it stands right now."""
        return build_realtime_instructions(self.scenario, self.jlpt_level, self.context_items)

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

    def _help_speed(self) -> float:
        """The rate a わからない turn is spoken at.

        Help delivered at conversational pace is not much help -- the learner
        pressed the button because they could not follow. Clamped into the same
        range the slider offers: at the slowest setting there is nothing left
        to give, and below it the speech smears rather than clarifies.
        """
        return self._clamp_speed(self.speed * self.settings.realtime_help_speed_factor)

    async def _set_output_speed(self, upstream: Any, speed: float) -> None:
        """Change the tutor's speaking rate and nothing else.

        The one narrow translation into ``session.update`` that both the
        slider and the help turn go through, so neither can widen it.
        """
        await upstream.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "audio": {"output": {"speed": speed}},
                    },
                }
            )
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
                "instructions": self._instructions(),
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
                if not self._help_speed_active:
                    # While a わからない turn is running the tutor is on the
                    # help rate; restoring afterwards picks up the new value.
                    await self._set_output_speed(upstream, self.speed)
                await self.send_json({"type": "app.speed.changed", "speed": self.speed})
                continue

            if event_type == "app.session.context":
                # The learner has just been handed something. Same shape as the
                # speed and eagerness translations: validated here, never
                # forwarded as the browser sent it.
                await self._add_context(upstream, _as_int(event.get("attachment_id")))
                continue

            if event_type == "app.session.help":
                # わからない. Built here from the trusted session prompt, never
                # from anything the browser sent.
                await self._request_help(upstream)
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

    async def _request_help(self, upstream: Any) -> None:
        """Answer a わからない press with one deliberately scaffolded response.

        The stage advances with every press and only resets once the learner
        speaks again, so pressing twice in a row really does get more help
        rather than the same help worded differently.

        A response that is still being generated is cancelled first: the
        learner pressed the button *because* of what they are hearing, and the
        API refuses a second response while one is active anyway. The actual
        ``response.create`` then rides on the ``response.done`` that the
        cancellation produces.
        """
        self.help_stage = min(MAX_HELP_STAGE, self.help_stage + 1)
        await self.send_json(
            {
                "type": "app.help.stage",
                "stage": self.help_stage,
                "max_stage": MAX_HELP_STAGE,
            }
        )

        if self._response_active:
            self._help_pending = True
            await upstream.send(json.dumps({"type": "response.cancel"}))
            return

        await self._send_help_response(upstream)

    async def _send_help_response(self, upstream: Any) -> None:
        """Ask for the one response that carries the scaffolding instructions.

        Slowed down first. There is no per-response speed in the Realtime API,
        so it goes through ``session.update`` and is put back on the
        ``response.done`` this response produces -- the next ordinary turn is
        at the learner's own rate again.
        """
        self._help_pending = False
        await self._set_output_speed(upstream, self._help_speed())
        self._help_speed_active = True
        self._help_turn_stage = self.help_stage
        await upstream.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        # `instructions` replaces the session prompt for this
                        # response only, so the whole frame is rebuilt here.
                        "instructions": build_help_instructions(
                            self.scenario,
                            self.jlpt_level,
                            self.help_stage,
                            self.context_items,
                        )
                    },
                }
            )
        )

    async def _restore_speed(self, upstream: Any) -> None:
        """Put the tutor back on the learner's own rate after a help turn."""
        self._help_speed_active = False
        await self._set_output_speed(upstream, self.speed)

    async def _reset_help(self) -> None:
        """Forget the escalation: the learner is talking again."""
        if self.help_stage == 0:
            return
        self.help_stage = 0
        await self.send_json(
            {"type": "app.help.stage", "stage": 0, "max_stage": MAX_HELP_STAGE}
        )

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

            if event_type == "response.created":
                self._response_active = True
            elif event_type == "response.done":
                self._response_active = False
                # The transcript for this response has already been through
                # `_emit_turn`, so the marker has done its job.
                self._help_turn_stage = None
                await self._handle_response_done(event)
                if self._help_pending:
                    await self._send_help_response(upstream)
                elif self._help_speed_active:
                    await self._restore_speed(upstream)
            elif event_type == USER_TRANSCRIPT_EVENT:
                await self._emit_turn("user", event.get("transcript", ""))
            elif event_type in ASSISTANT_TRANSCRIPT_EVENTS:
                await self._emit_turn("assistant", event.get("transcript", ""))
            elif event_type == "error":
                error = event.get("error") or {}
                logger.error("Realtime API error: %s", error)
                if self._help_pending:
                    # The cancellation we were waiting on failed, so the
                    # `response.done` that would have carried the help request
                    # is never coming. Ask for it now rather than leaving the
                    # button dead for the rest of the session.
                    self._response_active = False
                    await self._send_help_response(upstream)

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
        if role == "user":
            # Only a turn that carried words counts as getting past the spot.
            # The VAD commits background noise as a turn too, and those
            # transcribe to nothing -- resetting on one would silently undo the
            # escalation while the learner sits there pressing the button.
            await self._reset_help()
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
                        "help_stages": MAX_HELP_STAGE,
                        "help_speed_factor": self.settings.realtime_help_speed_factor,
                        "context_items": [item.model_dump() for item in self.context_items],
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
                # Including anything handed over mid-session, which the echoed
                # instructions predate.
                "context_items": [item.model_dump() for item in self.context_items],
            }
        )
        if self.client_ws.client_state is WebSocketState.CONNECTED:
            try:
                await self.client_ws.close()
            except RuntimeError:
                pass
