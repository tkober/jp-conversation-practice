"""Pydantic models for the HTTP API and the structured LLM output."""

from __future__ import annotations

from datetime import datetime
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


# --- Settings ---------------------------------------------------------------


class SettingsUpdate(BaseModel):
    """Patch for the settings row.

    Every field is optional and nullable: omitting a field leaves it unchanged,
    while sending ``null`` (or an empty string) clears the override so the
    environment default applies again.
    """

    openai_api_key: str | None = None
    realtime_model: str | None = None
    analysis_model: str | None = None
    scenario_assistant_model: str | None = None
    transcription_model: str | None = None
    tts_model: str | None = None
    realtime_voice: str | None = None
    realtime_speed: float | None = None
    realtime_vad_eagerness: str | None = None
    wanikani_api_token: str | None = None
    ankiconnect_url: str | None = None
    anki_deck_name: str | None = None


class SettingsView(BaseModel):
    """The effective settings, safe to send to the browser.

    Secrets are never returned in full — only whether one is set, where it came
    from, and a masked hint so the user can tell which key is stored.
    """

    realtime_model: str
    analysis_model: str
    scenario_assistant_model: str
    transcription_model: str
    tts_model: str
    realtime_voice: str
    realtime_speed: float
    realtime_vad_eagerness: str
    ankiconnect_url: str
    anki_deck_name: str

    openai_api_key_set: bool
    openai_api_key_hint: str | None = None
    openai_api_key_from_env: bool
    wanikani_api_token_set: bool
    wanikani_api_token_hint: str | None = None
    wanikani_api_token_from_env: bool

    speed_min: float
    speed_max: float


# --- Scenarios ---------------------------------------------------------------


class ScenarioView(BaseModel):
    id: int
    slug: str
    title: str
    summary: str
    prompt: str
    is_builtin: bool
    is_customized: bool


class ScenarioCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=300)
    prompt: str = Field(min_length=1)


class ScenarioUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    summary: str | None = Field(default=None, max_length=300)
    prompt: str | None = Field(default=None, min_length=1)


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ScenarioAssistantRequest(BaseModel):
    """A turn in the scenario editor's side conversation.

    ``draft`` is the prompt currently in the editor, sent every time so the
    assistant reasons about what the user sees rather than about its own last
    suggestion.
    """

    draft: str = ""
    title: str = ""
    messages: list[AssistantMessage] = Field(default_factory=list)


class ScenarioAssistantReply(BaseModel):
    reply: str
    suggested_prompt: str | None = Field(
        default=None,
        description="A full replacement draft, when the assistant produced one.",
    )


# --- Sessions ----------------------------------------------------------------


class SessionCreate(BaseModel):
    scenario_id: int | None = None
    scenario_title: str = ""
    scenario_prompt: str = ""
    jlpt_level: JlptLevel = "N5"
    model: str = ""
    voice: str = ""
    speed: float = 1.0
    vad_eagerness: str = ""
    instructions: str = ""
    duration_seconds: float = 0
    cost_usd: float = 0
    usage: dict = Field(default_factory=dict)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    analysis: dict | None = None


class SessionSummary(BaseModel):
    """List view — deliberately without transcript or analysis payloads."""

    id: int
    scenario_title: str
    jlpt_level: str
    model: str
    voice: str
    started_at: datetime
    duration_seconds: float
    cost_usd: float
    turn_count: int
    has_analysis: bool


class SessionDetail(SessionSummary):
    scenario_prompt: str
    speed: float
    vad_eagerness: str
    instructions: str
    usage: dict
    transcript: list[TranscriptTurn]
    analysis: dict | None


class AnkiExportRequest(BaseModel):
    cards: list[AnkiCard]
    deck_name: str | None = None
    tags: list[str] = Field(default_factory=lambda: ["ai-conversation"])


class AnkiExportResponse(BaseModel):
    added: int
    duplicates: int
    deck_name: str
    note_ids: list[int | None] = Field(default_factory=list)
