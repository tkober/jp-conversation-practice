"""The writing assistant built into the scenario editor.

Its job is narrow: help phrase a role-play scenario that produces good
conversations. The system prompt below encodes what this project learned the
hard way — that a scenario listing the steps of an interaction gets executed
literally, producing the same canned sequence every session and questions that
make no sense for what the learner actually did.

Runs on its own model (``scenario_assistant_model``), separate from the live
tutor: it writes prose rather than driving a conversation, so a stronger and
slower model is often worth it here.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .models import ScenarioAssistantReply, ScenarioAssistantRequest
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 20

SYSTEM_PROMPT = """You help a German-speaking learner of Japanese write scenario prompts for a
spoken role-play practice app. A scenario prompt is the instruction given to a
realtime voice model that will then play a character while the learner speaks
Japanese with it.

# What makes a good scenario prompt
- It describes WHO the model is, WHERE it is, and what it wants — a role and a
  situation. It never lists the steps of an interaction.
- It is written in English (the model reads it), in second person ("You are
  the clerk at ...").
- It is short: three to five sentences is plenty.
- It leaves room for the conversation to go differently every time.

# The mistake to avoid, always
Do NOT write sequences of things to ask. A prompt like "greet them, ask whether
to heat the food, ask if they need a bag, ask about chopsticks, take payment"
gets executed literally: the model works through the list regardless of what
the learner says, offering to heat an iced coffee and handing out chopsticks
with a drink, in the same order every single session.
Write "Serve them the way a real clerk would; what you offer depends on what
they are actually buying" instead. Roles generalise, checklists fossilise.
If the user's draft contains such a sequence, say so plainly and show them the
role-based rewrite.

# How to reply
Talk to the user IN GERMAN — that is the language of the app's interface and
of this conversation. The scenario prompt itself always stays ENGLISH.
Be concrete and brief. When you propose wording, put the complete new prompt
into `suggested_prompt` (the full replacement text, not a fragment or a diff),
so the editor can offer it as a one-click replacement. When you are only
answering a question or giving feedback without proposing new text, leave
`suggested_prompt` empty."""


class _AssistantOutput(BaseModel):
    """Structured shape the model is forced to produce."""

    reply: str
    suggested_prompt: str = ""


class ScenarioAssistantError(RuntimeError):
    """Raised when the assistant model cannot be reached or misbehaves."""


class ScenarioAssistant:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    async def respond(self, request: ScenarioAssistantRequest) -> ScenarioAssistantReply:
        if not self.config.openai_api_key:
            raise ScenarioAssistantError("No OpenAI API key is configured.")

        content = await self._post(self._payload(request))

        try:
            parsed = _AssistantOutput.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.error("Assistant returned unusable JSON: %s", content[:300])
            raise ScenarioAssistantError(
                "The assistant returned an unexpected response."
            ) from exc

        suggestion = parsed.suggested_prompt.strip()
        return ScenarioAssistantReply(
            reply=parsed.reply.strip(),
            suggested_prompt=suggestion or None,
        )

    def _payload(self, request: ScenarioAssistantRequest) -> dict[str, Any]:
        draft = request.draft.strip() or "(the draft is still empty)"
        title = request.title.strip() or "(no title yet)"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                # Resent every turn so the assistant reasons about what is in
                # the editor now, not about its own earlier suggestion.
                "content": f"Current scenario title: {title}\n\nCurrent draft:\n{draft}",
            },
        ]
        for message in request.messages[-MAX_HISTORY_MESSAGES:]:
            messages.append({"role": message.role, "content": message.content})

        return {
            "model": self.config.scenario_assistant_model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "scenario_assistant_reply",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "reply": {
                                "type": "string",
                                "description": "The answer to the user, in German.",
                            },
                            "suggested_prompt": {
                                "type": "string",
                                "description": (
                                    "Complete replacement prompt in English, or an "
                                    "empty string when not proposing new wording."
                                ),
                            },
                        },
                        "required": ["reply", "suggested_prompt"],
                        "additionalProperties": False,
                    },
                },
            },
        }

    async def _post(self, payload: dict[str, Any]) -> str:
        url = f"{self.config.openai_api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.openai_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Assistant request failed: HTTP %s %s",
                exc.response.status_code,
                exc.response.text[:300],
            )
            raise ScenarioAssistantError(
                f"The assistant model returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise ScenarioAssistantError(f"Could not reach the OpenAI API: {exc}") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ScenarioAssistantError("The assistant model returned no choices.") from exc

        if message.get("refusal"):
            raise ScenarioAssistantError(f"The assistant refused: {message['refusal']}")

        content = message.get("content")
        if not content:
            raise ScenarioAssistantError("The assistant model returned an empty response.")
        return content
