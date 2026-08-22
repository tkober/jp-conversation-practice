"""Pydantic models for the HTTP API and the structured LLM output."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

JlptLevel = Literal["N5", "N4", "N3", "N2"]


class RubySegment(BaseModel):
    """One piece of a line, with its reading when it contains kanji.

    ``text`` is always a verbatim slice of the turn, so the segments joined
    together are the original line again.
    """

    text: str
    reading: str | None = None


class ContextItem(BaseModel):
    """One piece of context material as the tutor and the export see it.

    The prepared form of a :class:`~app.db.ScenarioAttachment`: the bytes stay
    in the database for the learner's screen, this is what reaches the prompt.
    ``introduced_at`` is None for material that was there from the first turn
    and holds the elapsed seconds for material handed over mid-conversation --
    the one thing about a session that the stored ``instructions`` cannot say,
    since those were built before it arrived.
    """

    id: int
    kind: Literal["image", "text"] = "image"
    title: str = ""
    description: str = ""
    introduced_at: float | None = None


class TranscriptTurn(BaseModel):
    """A single spoken turn captured during the realtime session."""

    role: Literal["user", "assistant"]
    text: str
    timestamp: float | None = None
    # Furigana, derived from ``text`` (see furigana.py) rather than stored:
    # None means "nothing to annotate here", and the UI shows the plain text.
    ruby: list[RubySegment] | None = None
    # Set on an assistant turn that answers a わからない press, to the stage it
    # was given at. Stored with the session, unlike the furigana: it records
    # what happened rather than deriving from the text.
    help_stage: int | None = None


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
    context_items: list[ContextItem] = Field(default_factory=list)


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
    realtime_help_speed_factor: float | None = None
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
    realtime_help_speed_factor: float
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
    help_speed_factor_min: float
    help_speed_factor_max: float


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


# --- Context material --------------------------------------------------------


class AttachmentView(BaseModel):
    """One piece of scenario material, without its bytes.

    The image itself is fetched separately from ``/api/attachments/{id}/file``
    so that listing a scenario's material does not drag several megabytes of
    base64 through every request that only needs the labels.
    """

    id: int
    scenario_id: int
    kind: Literal["image", "text"]
    title: str
    description: str
    body: str
    media_type: str
    byte_size: int
    available_from_start: bool
    sort_order: int
    analysis_error: str | None = Field(
        default=None,
        description=(
            "Only set on the response to an upload or a re-evaluation, when the "
            "material was stored but the model could not describe it. The "
            "attachment is kept either way so the file does not have to be "
            "picked again."
        ),
    )


class AttachmentUpdate(BaseModel):
    """Patch for one attachment; omitted fields stay as they are."""

    title: str | None = Field(default=None, max_length=120)
    description: str | None = None
    available_from_start: bool | None = None
    sort_order: int | None = None


class TextAttachmentCreate(BaseModel):
    """A pasted piece of text, as opposed to an uploaded file."""

    body: str = Field(min_length=1)
    title: str = Field(default="", max_length=120)
    hint: str = Field(default="", max_length=500)
    available_from_start: bool = True


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
    context_items: list[ContextItem] = Field(default_factory=list)
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
    context_items: list[ContextItem]
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
