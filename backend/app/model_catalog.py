"""Selectable models per configuration slot.

The Settings screen offers a dropdown rather than a text field, and the list
behind it is built from two sources that answer different questions.

``GET /v1/models`` answers *what this account may call*. It is authoritative
and always current, but each entry carries only ``id``, ``created``,
``owned_by`` and ``shutdown_date`` -- no price, no modality, no capability. The
ids are not self-describing either: ``gpt-realtime-whisper`` and
``gpt-realtime-translate`` both match "realtime" without being conversation
models, so a prefix filter alone would offer them as tutors.

The curated table below answers *what is worth picking and what it costs*.
That is knowledge the API does not expose, so it is written down here.

Merging the two keeps both properties: a model released after the last deploy
is still selectable, and the ones we actually know something about come first
with a description. Anything the filters drop can still be typed into the free
-text field the UI keeps as an escape hatch.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from .pricing import MODEL_RATES, rates_for

logger = logging.getLogger(__name__)

# The live list changes on OpenAI's release schedule, not ours, so a short
# cache is plenty and keeps the Settings screen off the network on every open.
_CACHE_TTL_SECONDS = 15 * 60

# Dated ids like `gpt-5-2025-08-07` pin an alias that is already in the list,
# so including them would roughly double every dropdown without adding a single
# capability. Someone who deliberately wants a pinned snapshot types it into
# the free-text field.
_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")

# A model id reaches the filesystem: VoiceSampleService caches previews under
# `.voice-samples/<tts_model>/`. Validating the shape keeps a hand-crafted
# settings PUT from escaping that directory, the same reason voices.py checks
# its ids.
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_valid_model_id(model: str) -> bool:
    """Whether ``model`` is shaped like a model id and safe in a path."""
    return bool(MODEL_ID_PATTERN.fullmatch(model))


@dataclass(frozen=True)
class CuratedModel:
    """A model we have an opinion about.

    `description` is user-facing and therefore German, like the voice
    descriptions and the scenario titles.
    """

    id: str
    label: str
    description: str


@dataclass(frozen=True)
class ModelSlot:
    """One configurable model, and how to recognise candidates for it.

    `prefixes` / `contains` select from the live list, `excludes` removes the
    ids that match by name but not by purpose. `cost_tracked` marks the slot
    whose model `CostTracker` actually bills, which is the only slot where
    showing a price -- or warning that none is known -- means anything.
    """

    key: str
    label: str
    hint: str
    curated: tuple[CuratedModel, ...]
    prefixes: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    cost_tracked: bool = False

    def matches(self, model_id: str) -> bool:
        """Whether a live-list id belongs in this slot's dropdown."""
        if any(fragment in model_id for fragment in self.excludes):
            return False
        if _SNAPSHOT_SUFFIX.search(model_id):
            return False
        return (
            model_id in self.exact
            or any(model_id.startswith(prefix) for prefix in self.prefixes)
            or any(fragment in model_id for fragment in self.contains)
        )


# Chat models that can drive Structured Outputs. Shared by the two text slots:
# both send a normal chat completion, they only differ in what they write.
_CHAT_PREFIXES = ("gpt-5", "gpt-4.1", "gpt-4o", "o3", "o4")
_CHAT_EXCLUDES = (
    "-transcribe",
    "-tts",
    "-audio",
    "-realtime",
    "-search",
    "-image",
    "-codex",
    "-deep-research",
    "-instruct",
)


SLOTS: tuple[ModelSlot, ...] = (
    ModelSlot(
        key="realtime_model",
        label="Konversation (Realtime)",
        hint=(
            "Führt das Live-Gespräch. Der Preis gilt pro 1M Audio-Token "
            "(Eingabe / Ausgabe) und ist das, was die Kostenanzeige abrechnet."
        ),
        curated=(
            CuratedModel(
                "gpt-realtime-2.1-mini",
                "gpt-realtime-2.1-mini",
                "Standard. Günstig, aber das schwächste Glied in der Kohärenz.",
            ),
            CuratedModel(
                "gpt-realtime-2.1",
                "gpt-realtime-2.1",
                "Aktuelle Vollversion. 3,2x teurer pro Audio-Token, dafür deutlich "
                "kohärenter — die erste Wahl, wenn nicht die Formulierung, sondern "
                "das Denken des Tutors das Problem ist.",
            ),
            CuratedModel(
                "gpt-realtime",
                "gpt-realtime",
                "Der unversionierte Alias derselben Klasse: gleiche Audio-Preise wie "
                "2.1, aber mit Abschaltdatum. Für Neues 2.1 nehmen.",
            ),
        ),
        prefixes=("gpt-realtime",),
        # Speech-to-speech only: -whisper transcribes and -translate translates.
        excludes=("-whisper", "-translate"),
        cost_tracked=True,
    ),
    ModelSlot(
        key="analysis_model",
        label="Auswertung",
        hint="Erzeugt Feedback, Grammatik-Hinweise und Anki-Karten nach der Session.",
        curated=(
            CuratedModel(
                "gpt-4o-mini", "gpt-4o-mini", "Standard. Schnell und günstig für die Auswertung."
            ),
            CuratedModel(
                "gpt-4o", "gpt-4o", "Genauer bei Grammatik-Erklärungen, spürbar teurer."
            ),
            CuratedModel(
                "gpt-5-mini", "gpt-5-mini", "Neuere Generation, gutes Verhältnis für diese Aufgabe."
            ),
        ),
        prefixes=_CHAT_PREFIXES,
        excludes=_CHAT_EXCLUDES,
    ),
    ModelSlot(
        key="scenario_assistant_model",
        label="Szenario-Assistent",
        hint=(
            "Hilft im Szenario-Editor beim Formulieren und wertet das Material "
            "eines Szenarios aus. Schreibt Prosa statt zu sprechen — ein "
            "stärkeres Modell lohnt sich hier eher. Für Bild-Material muss es "
            "Bilder lesen können."
        ),
        curated=(
            CuratedModel("gpt-4o", "gpt-4o", "Standard. Schreibt brauchbare Szenario-Prosa."),
            CuratedModel("gpt-5", "gpt-5", "Stärker im Umformulieren und im Erkennen von Checklisten."),
            CuratedModel("gpt-4o-mini", "gpt-4o-mini", "Günstiger, knappere Vorschläge."),
        ),
        prefixes=_CHAT_PREFIXES,
        excludes=_CHAT_EXCLUDES,
    ),
    ModelSlot(
        key="transcription_model",
        label="Transkription",
        hint="Wandelt deine Sprache in Text für Transkript und Auswertung.",
        curated=(
            CuratedModel(
                "gpt-4o-mini-transcribe",
                "gpt-4o-mini-transcribe",
                "Standard. Günstig und für Japanisch ausreichend genau.",
            ),
            CuratedModel(
                "gpt-4o-transcribe", "gpt-4o-transcribe", "Genauer bei undeutlicher Aussprache."
            ),
            CuratedModel("whisper-1", "whisper-1", "Älteres Modell, robust und breit erprobt."),
        ),
        contains=("transcribe",),
        exact=("whisper-1",),
        # Diarisation splits speakers apart; the realtime input stream is one.
        excludes=("-diarize",),
    ),
    ModelSlot(
        key="tts_model",
        label="Stimmproben (TTS)",
        hint="Erzeugt die Hörproben in der Stimmauswahl. Wird einmal pro Stimme gerendert.",
        curated=(
            CuratedModel(
                "gpt-4o-mini-tts",
                "gpt-4o-mini-tts",
                "Standard. Versteht die Anweisung, wie die Probe klingen soll.",
            ),
            CuratedModel("tts-1", "tts-1", "Älter und schneller, ignoriert Stil-Anweisungen."),
            CuratedModel("tts-1-hd", "tts-1-hd", "Wie tts-1, höhere Audioqualität."),
        ),
        contains=("tts",),
    ),
)

SLOTS_BY_KEY = {slot.key: slot for slot in SLOTS}


def price_hint(model_id: str) -> str | None:
    """The audio rates this app would bill ``model_id`` at, or None if unknown.

    Only meaningful for the realtime slot: `MODEL_RATES` is the table
    `CostTracker` bills against, and an entry missing from it silently falls
    back to the mini rates. Saying so in the dropdown is cheaper than
    discovering it on the session screen.
    """
    if model_id not in MODEL_RATES:
        return None
    rates = rates_for(model_id)
    return f"${rates.audio_input:g} / ${rates.audio_output:g} pro 1M Audio-Token"


@dataclass
class ModelOption:
    """One entry in a dropdown."""

    id: str
    label: str
    description: str | None
    curated: bool
    price_hint: str | None = None
    rates_known: bool | None = None
    # From the live list: the date after which OpenAI retires the model.
    shutdown_date: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "curated": self.curated,
            "price_hint": self.price_hint,
            "rates_known": self.rates_known,
            "shutdown_date": self.shutdown_date,
        }


@dataclass
class CatalogResult:
    """Every slot's options, plus why the live list may be missing."""

    slots: list[dict[str, object]] = field(default_factory=list)
    live_ok: bool = False
    live_detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "slots": self.slots,
            "live_ok": self.live_ok,
            "live_detail": self.live_detail,
        }


class ModelListError(RuntimeError):
    """Raised when the live model list cannot be fetched."""


class ModelCatalog:
    """Builds the dropdown contents, caching the live list in process."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] | None = None
        self._cache_time = 0.0
        self._cache_key = ""
        self._lock = asyncio.Lock()

    async def build(self, api_base: str, api_key: str) -> CatalogResult:
        """Curated entries first, live extras after, never failing on network.

        A missing key or an unreachable API degrades to the curated list rather
        than an empty dropdown -- the same trade the WaniKani filter makes.
        """
        live: dict[str, str | None] = {}
        detail: str | None = None
        ok = False

        if not api_key:
            detail = "Kein OpenAI-API-Key hinterlegt."
        else:
            try:
                live = await self._live_models(api_base, api_key)
                ok = True
            except ModelListError as exc:
                detail = str(exc)

        result = CatalogResult(live_ok=ok, live_detail=detail)
        for slot in SLOTS:
            result.slots.append(
                {
                    "key": slot.key,
                    "label": slot.label,
                    "hint": slot.hint,
                    "cost_tracked": slot.cost_tracked,
                    "options": [option.as_dict() for option in _options_for(slot, live)],
                }
            )
        return result

    async def _live_models(self, api_base: str, api_key: str) -> dict[str, str | None]:
        """Model ids to shutdown date, cached per key so a new key refetches."""
        cache_key = f"{api_base}\n{api_key}"
        async with self._lock:
            fresh = (
                self._cache is not None
                and self._cache_key == cache_key
                and time.time() - self._cache_time < _CACHE_TTL_SECONDS
            )
            if fresh and self._cache is not None:
                return self._cache

            models = await _fetch_models(api_base, api_key)
            self._cache = models
            self._cache_time = time.time()
            self._cache_key = cache_key
            return models


def _options_for(slot: ModelSlot, live: dict[str, str | None]) -> list[ModelOption]:
    """Curated entries in their hand-picked order, then live extras sorted."""
    options: list[ModelOption] = []
    seen: set[str] = set()

    for entry in slot.curated:
        options.append(_option(slot, entry.id, entry.label, entry.description, True, live))
        seen.add(entry.id)

    extras = sorted(
        model_id for model_id in live if model_id not in seen and slot.matches(model_id)
    )
    for model_id in extras:
        options.append(_option(slot, model_id, model_id, None, False, live))

    return options


def _option(
    slot: ModelSlot,
    model_id: str,
    label: str,
    description: str | None,
    curated: bool,
    live: dict[str, str | None],
) -> ModelOption:
    return ModelOption(
        id=model_id,
        label=label,
        description=description,
        curated=curated,
        price_hint=price_hint(model_id) if slot.cost_tracked else None,
        rates_known=(model_id in MODEL_RATES) if slot.cost_tracked else None,
        shutdown_date=live.get(model_id),
    )


async def _fetch_models(api_base: str, api_key: str) -> dict[str, str | None]:
    """Ask the API which models this key may call."""
    url = f"{api_base.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("Model list failed: HTTP %s", exc.response.status_code)
        raise ModelListError(
            f"Modellliste nicht abrufbar (HTTP {exc.response.status_code})."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Model list failed: %s", exc)
        raise ModelListError("OpenAI-API für die Modellliste nicht erreichbar.") from exc

    models: dict[str, str | None] = {}
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not is_valid_model_id(model_id):
            continue
        shutdown = entry.get("shutdown_date")
        models[model_id] = shutdown if isinstance(shutdown, str) else None
    return models
