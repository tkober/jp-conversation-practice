"""Post-session analysis: feedback, grammar notes and Anki card extraction.

Uses the Chat Completions API with Structured Outputs so the response is
guaranteed to match :class:`~app.models.SessionAnalysis`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from .models import ContextItem, SessionAnalysis, TranscriptTurn
from .prompts import ANALYSIS_SYSTEM_PROMPT, build_analysis_user_prompt
from .runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

ROLE_LABELS = {"user": "Learner", "assistant": "Tutor"}


class AnalysisError(RuntimeError):
    """Raised when the analysis model call fails."""


def format_transcript(transcript: list[TranscriptTurn]) -> str:
    """Render the transcript as a readable dialogue for the analysis prompt."""
    lines = []
    for turn in transcript:
        label = ROLE_LABELS.get(turn.role, turn.role)
        text = turn.text.strip()
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _strict_schema(model: type[SessionAnalysis]) -> dict[str, Any]:
    """Turn a Pydantic JSON schema into an OpenAI ``strict`` compatible one.

    Strict mode requires every object to list all its properties in `required`
    and to set `additionalProperties: false`.
    """

    def tighten(node: Any) -> Any:
        if isinstance(node, list):
            return [tighten(item) for item in node]
        if not isinstance(node, dict):
            return node

        node = {key: tighten(value) for key, value in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"].keys())
            node["additionalProperties"] = False
        return node

    return tighten(model.model_json_schema())


class AnalysisService:
    """Calls the analysis model and validates its structured response."""

    def __init__(self, settings: RuntimeConfig) -> None:
        self.settings = settings

    async def analyse(
        self,
        *,
        scenario: str,
        jlpt_level: str,
        transcript: list[TranscriptTurn],
        excluded_words: list[str],
        context_items: list[ContextItem] | None = None,
    ) -> SessionAnalysis:
        if not self.settings.openai_api_key:
            raise AnalysisError("OPENAI_API_KEY is not configured on the server.")

        payload = {
            "model": self.settings.analysis_model,
            "messages": [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_analysis_user_prompt(
                        scenario,
                        jlpt_level,
                        format_transcript(transcript),
                        excluded_words,
                        context_items or [],
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "session_analysis",
                    "strict": True,
                    "schema": _strict_schema(SessionAnalysis),
                },
            },
        }

        content = await self._post_chat_completion(payload)

        try:
            return SessionAnalysis.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.error("Analysis model returned unusable JSON: %s", content[:500])
            raise AnalysisError("The analysis model returned an unexpected response.") from exc

    async def _post_chat_completion(self, payload: dict[str, Any]) -> str:
        url = f"{self.settings.openai_api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            logger.error("Analysis request failed: HTTP %s %s", exc.response.status_code, detail)
            raise AnalysisError(
                f"Analysis request failed with HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise AnalysisError(f"Could not reach the OpenAI API: {exc}") from exc

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AnalysisError("The analysis model returned no choices.") from exc

        if message.get("refusal"):
            raise AnalysisError(f"The analysis model refused: {message['refusal']}")

        content = message.get("content")
        if not content:
            raise AnalysisError("The analysis model returned an empty response.")
        return content


def filter_known_cards(
    analysis: SessionAnalysis,
    known_words: set[str],
) -> tuple[SessionAnalysis, list[str]]:
    """Drop Anki cards for words WaniKani reports as known, and deduplicate.

    Matching is done on both the expression and its reading, because WaniKani
    stores kana-only vocabulary under its kana form.
    """
    kept = []
    removed: list[str] = []
    seen: set[str] = set()

    for card in analysis.anki_cards:
        expression = card.expression.strip()
        if not expression or expression in seen:
            continue
        seen.add(expression)

        if expression in known_words or card.reading.strip() in known_words:
            removed.append(expression)
            continue
        kept.append(card)

    return analysis.model_copy(update={"anki_cards": kept}), removed
