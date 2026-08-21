"""AnkiConnect client for pushing generated cards into the local Anki desktop app.

AnkiConnect exposes a single JSON-RPC style endpoint on http://localhost:8765
and requires the Anki desktop app to be running with the add-on installed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .models import AnkiCard

logger = logging.getLogger(__name__)

ANKICONNECT_VERSION = 6

# Field layout of the note type this PoC creates on first use.
MODEL_FIELDS = ["Expression", "Reading", "Meaning", "ContextSentence"]

_CARD_FRONT = "<div class='expression'>{{Expression}}</div>"
_CARD_BACK = (
    "{{FrontSide}}<hr id='answer'>"
    "<div class='reading'>{{Reading}}</div>"
    "<div class='meaning'>{{Meaning}}</div>"
    "<div class='context'>{{ContextSentence}}</div>"
)
_CARD_CSS = (
    ".card { font-family: 'Hiragino Sans', 'Noto Sans JP', sans-serif; "
    "font-size: 22px; text-align: center; color: #1c1c1e; background: #fdfdfd; }\n"
    ".expression { font-size: 44px; margin-bottom: 8px; }\n"
    ".reading { font-size: 26px; color: #3a6ea5; }\n"
    ".meaning { margin-top: 10px; }\n"
    ".context { margin-top: 14px; font-size: 18px; color: #666; }"
)


class AnkiConnectError(RuntimeError):
    """Raised when AnkiConnect is unreachable or returns an error."""


class AnkiConnectClient:
    """Thin async wrapper around the AnkiConnect JSON-RPC endpoint."""

    def __init__(self, url: str, model_name: str) -> None:
        self.url = url
        self.model_name = model_name

    async def add_cards(
        self,
        cards: list[AnkiCard],
        deck_name: str,
        tags: list[str],
    ) -> tuple[list[int | None], int]:
        """Create deck and note type if needed, then add the notes.

        Returns the note ids reported by AnkiConnect (``None`` for entries that
        Anki refused as duplicates) and the number of such duplicates.
        """
        async with httpx.AsyncClient(timeout=20.0) as client:
            await self._ensure_deck(client, deck_name)
            await self._ensure_model(client)

            notes = [self._build_note(card, deck_name, tags) for card in cards]
            if not notes:
                return [], 0

            note_ids = await self._invoke(client, "addNotes", {"notes": notes})

        if not isinstance(note_ids, list):
            raise AnkiConnectError("AnkiConnect returned an unexpected addNotes result.")

        duplicates = sum(1 for note_id in note_ids if note_id is None)
        return note_ids, duplicates

    # --- internals -------------------------------------------------------

    def _build_note(self, card: AnkiCard, deck_name: str, tags: list[str]) -> dict[str, Any]:
        return {
            "deckName": deck_name,
            "modelName": self.model_name,
            "fields": {
                "Expression": card.expression,
                "Reading": card.reading,
                "Meaning": card.meaning,
                "ContextSentence": card.context_sentence,
            },
            "tags": tags,
            "options": {"allowDuplicate": False},
        }

    async def _ensure_deck(self, client: httpx.AsyncClient, deck_name: str) -> None:
        decks = await self._invoke(client, "deckNames")
        if deck_name not in (decks or []):
            await self._invoke(client, "createDeck", {"deck": deck_name})

    async def _ensure_model(self, client: httpx.AsyncClient) -> None:
        models = await self._invoke(client, "modelNames")
        if self.model_name in (models or []):
            return
        await self._invoke(
            client,
            "createModel",
            {
                "modelName": self.model_name,
                "inOrderFields": MODEL_FIELDS,
                "css": _CARD_CSS,
                "cardTemplates": [
                    {
                        "Name": "Recognition",
                        "Front": _CARD_FRONT,
                        "Back": _CARD_BACK,
                    }
                ],
            },
        )

    async def _invoke(
        self,
        client: httpx.AsyncClient,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        payload = {"action": action, "version": ANKICONNECT_VERSION, "params": params or {}}
        try:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise AnkiConnectError(
                f"Could not reach AnkiConnect at {self.url}. "
                "Is Anki running with the AnkiConnect add-on installed?"
            ) from exc
        except ValueError as exc:
            raise AnkiConnectError("AnkiConnect returned a non-JSON response.") from exc

        if isinstance(body, dict) and body.get("error"):
            raise AnkiConnectError(f"AnkiConnect error on '{action}': {body['error']}")
        return body.get("result") if isinstance(body, dict) else None
