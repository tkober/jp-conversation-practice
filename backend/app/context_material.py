"""Turning an attachment into something the tutor can actually use.

The ticket's phrasing is the design: material is *evaluated* once and handed to
the tutor *prepared*, rather than passed through raw. Three reasons that is the
right way round here.

* The realtime tutor runs on ``gpt-realtime-2.1-mini`` by default, already the
  weakest link in coherence (see CLAUDE.md). Asking it to read a photographed
  Japanese menu while holding a conversation is asking for the failure mode
  the whole prompt frame exists to prevent.
* A description written once is stable across sessions, is the same text in
  the session export, and can be corrected by hand when the model misreads a
  price. An image re-interpreted every session is none of those.
* It keeps the tutor's prompt text-only, so ``build_help_instructions`` picks
  the material up for free.

The prompt below fights one specific thing. A menu *is* a list, and the lesson
this project learned the hard way is that a list in the prompt gets executed:
an early konbini scenario spelled out the steps of a checkout and the model
worked through them literally every session. So the description must read as
"this is what is in the scene", never as "this is what happens next" -- and the
model is told so twice, once here and once in the frame that consumes it.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .db import ScenarioAttachment
from .models import ContextItem
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


def to_context_item(
    row: ScenarioAttachment, introduced_at: float | None = None
) -> ContextItem:
    """The prompt-facing form of one stored attachment.

    Only the description travels; the bytes stay in the database and go to the
    learner's screen instead.
    """
    return ContextItem(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        description=row.description,
        introduced_at=introduced_at,
    )

# What OpenAI's vision input accepts. Anything else is rejected at upload
# rather than discovered as an API error minutes later.
SUPPORTED_IMAGE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)

# "low" downsamples to 512px, which is enough to see *that* something is a menu
# and not enough to read one. Reading the kanji is the entire point here, so
# the expensive setting is the only useful one -- and it is paid once per
# upload, not once per session.
IMAGE_DETAIL = "high"

SYSTEM_PROMPT = """You prepare context material for a spoken Japanese role-play practice app.

A learner is about to have a conversation with an AI tutor that plays a
character in a scenario -- a shop clerk, a waiter, a passer-by. The learner has
supplied a piece of material that belongs to that scene: a photo of a shelf, a
restaurant menu, a map excerpt, a ticket, a handwritten note. During the
session the LEARNER SEES THIS MATERIAL ON SCREEN. The tutor does not; it only
gets what you write.

Your job is to write, in ENGLISH, what is in this material, so that the tutor
can talk about it as something present in the room and can understand what the
learner is pointing at when they say これ or その赤いの.

# What to write
- Describe what the material IS and what is ON it. Be concrete and complete
  about the things that can be referred to: items, names, prices, numbers,
  places, directions, times.
- Keep Japanese in Japanese. Write a menu item as 唐揚げ定食 (からあげていしょく)
  – 850円, not as "fried chicken set". The tutor has to say these words out
  loud; a translation cannot be pronounced. Add a short English gloss in
  brackets only where the meaning is not obvious from the Japanese.
- Where the material has spatial structure -- a shelf, a map, a table layout --
  say where things are relative to each other. That is what makes 右の, 上の段,
  この先 usable.
- If something is unreadable, blurry or cut off, say so plainly. Never guess a
  price or a name. An invented item the learner cannot see on their screen is
  worse than an admitted gap.
- Be compact. Prose and short lists of facts, no headings, no commentary about
  the image quality, no advice for the tutor.

# The one thing you must not do
Do NOT write a sequence of actions, steps, or things to ask about. No "first
the learner orders, then you ask about drinks". No "ask them which item they
want". A prompt that contains a sequence gets executed literally, in the same
order every session, regardless of what the learner actually says. You are
describing a THING THAT EXISTS, not an interaction. If you catch yourself
writing a verb in the imperative, you have started writing the wrong document.

# The title
Also propose a short title for this material, IN GERMAN, of at most a handful
of words -- the learner sees it as a label in the app. "Speisekarte des
Izakaya", "Regal mit Getränken", "Karte um den Bahnhof"."""


class MaterialAnalysis(BaseModel):
    """What the evaluation produces: a German label and English prose."""

    title: str
    description: str


class ContextMaterialError(RuntimeError):
    """Raised when the material could not be evaluated."""


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Short label for the material, in German.",
        },
        "description": {
            "type": "string",
            "description": (
                "What the material is and what is on it, in English, for the "
                "tutor's prompt. Facts about the scene, never a sequence of steps."
            ),
        },
    },
    "required": ["title", "description"],
    "additionalProperties": False,
}


def data_url(media_type: str, data: bytes) -> str:
    """The inline form the vision API takes an image in."""
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


class ContextMaterialService:
    """Describes one attachment for the tutor.

    Runs on ``scenario_assistant_model`` rather than a slot of its own: this is
    the same job that slot already does -- write English prose that ends up in
    a scenario's prompt -- only the input is a photo instead of a draft. A
    model without vision configured there fails with the API's own message,
    which is the clearest thing anyone could say about it.
    """

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    async def describe_image(
        self, *, media_type: str, data: bytes, scenario_prompt: str, hint: str
    ) -> MaterialAnalysis:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": self._user_text(scenario_prompt, hint)},
            {
                "type": "image_url",
                "image_url": {"url": data_url(media_type, data), "detail": IMAGE_DETAIL},
            },
        ]
        return await self._run(content)

    async def describe_text(
        self, *, body: str, scenario_prompt: str, hint: str
    ) -> MaterialAnalysis:
        """Same treatment for pasted text.

        Text goes through the model too rather than straight into the prompt:
        a menu typed out by hand still needs readings attached and still needs
        to arrive as facts rather than as a list of things to offer.
        """
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{self._user_text(scenario_prompt, hint)}\n\n"
                    f"The material is this text:\n\n{body}"
                ),
            }
        ]
        return await self._run(content)

    def _user_text(self, scenario_prompt: str, hint: str) -> str:
        parts = ["Prepare this material for the tutor."]
        scenario = scenario_prompt.strip()
        if scenario:
            parts += [
                "",
                "It belongs to this scenario, which is what the tutor is playing:",
                scenario,
            ]
        note = hint.strip()
        if note:
            parts += [
                "",
                "The learner added this note about the material (it may be in "
                "German; use it to understand what matters about the material, "
                "and write your own output in the languages described above):",
                note,
            ]
        return "\n".join(parts)

    async def _run(self, content: list[dict[str, Any]]) -> MaterialAnalysis:
        if not self.config.openai_api_key:
            raise ContextMaterialError("No OpenAI API key is configured.")

        payload = {
            "model": self.config.scenario_assistant_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "context_material",
                    "strict": True,
                    "schema": _SCHEMA,
                },
            },
        }

        raw = await self._post(payload)

        try:
            parsed = MaterialAnalysis.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.error("Material evaluation returned unusable JSON: %s", raw[:300])
            raise ContextMaterialError(
                "The model returned an unexpected response."
            ) from exc

        return MaterialAnalysis(
            title=parsed.title.strip()[:120],
            description=parsed.description.strip()[
                : self.config.attachment_description_max_chars
            ],
        )

    async def _post(self, payload: dict[str, Any]) -> str:
        url = f"{self.config.openai_api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.openai_api_key}"}
        try:
            # Longer than the other calls: a high-detail photo is a lot of
            # tiles, and a failed upload means the user has to pick the file
            # again.
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Material evaluation failed: HTTP %s %s",
                exc.response.status_code,
                exc.response.text[:300],
            )
            raise ContextMaterialError(
                f"The model returned HTTP {exc.response.status_code}. If it has no "
                "vision support, pick a different model for the scenario assistant."
            ) from exc
        except httpx.HTTPError as exc:
            raise ContextMaterialError(f"Could not reach the OpenAI API: {exc}") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ContextMaterialError("The model returned no choices.") from exc

        if message.get("refusal"):
            raise ContextMaterialError(f"The model refused: {message['refusal']}")

        content = message.get("content")
        if not content:
            raise ContextMaterialError("The model returned an empty response.")
        return content
