"""Pydantic models for the HTTP API and the structured LLM output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JlptLevel = Literal["N5", "N4", "N3", "N2"]


class TranscriptTurn(BaseModel):
    """A single spoken turn captured during the realtime session."""

    role: Literal["user", "assistant"]
    text: str
    timestamp: float | None = None


# --- Structured LLM output ---------------------------------------------------


class GrammarNote(BaseModel):
    original: str = Field(description="The learner's original utterance, verbatim.")
    correction: str = Field(description="The natural Japanese correction.")
    explanation: str = Field(description="Short explanation in German.")


class AnkiCard(BaseModel):
    expression: str = Field(description="Japanese expression, kanji where normal.")
    reading: str = Field(description="Reading in hiragana/katakana only.")
    meaning: str = Field(description="Meaning in German.")
    context_sentence: str = Field(description="Short Japanese example sentence.")


class SessionAnalysis(BaseModel):
    """The schema the analysis model is forced to fill via Structured Outputs."""

    summary: str
    grammar_notes: list[GrammarNote]
    anki_cards: list[AnkiCard]


# --- Requests / responses ----------------------------------------------------


class AnalysisRequest(BaseModel):
    scenario: str = ""
    jlpt_level: JlptLevel = "N5"
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    use_wanikani_filter: bool = True


class AnalysisResponse(SessionAnalysis):
    filtered_out: list[str] = Field(
        default_factory=list,
        description="Expressions removed because WaniKani reports them as known.",
    )
    wanikani_status: str = Field(
        default="disabled",
        description="One of: disabled, ok, error.",
    )
    wanikani_message: str | None = None


class AnkiExportRequest(BaseModel):
    cards: list[AnkiCard]
    deck_name: str | None = None
    tags: list[str] = Field(default_factory=lambda: ["ai-conversation"])


class AnkiExportResponse(BaseModel):
    added: int
    duplicates: int
    deck_name: str
    note_ids: list[int | None] = Field(default_factory=list)
