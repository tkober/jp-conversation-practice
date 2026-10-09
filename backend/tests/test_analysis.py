"""Tests for transcript formatting, strict schema generation and WaniKani filtering."""

from __future__ import annotations

from app.analysis import _strict_schema, filter_known_cards, format_transcript
from app.models import (
    AnkiCard,
    ContextEvent,
    ContextItem,
    GrammarNote,
    HelpEvent,
    SessionAnalysis,
    SpeechEvent,
)


def make_analysis(*expressions: tuple[str, str]) -> SessionAnalysis:
    return SessionAnalysis(
        summary="Gut gemacht.",
        grammar_notes=[GrammarNote(original="a", correction="b", explanation="c")],
        anki_cards=[
            AnkiCard(
                expression=expression,
                reading=reading,
                meaning="Bedeutung",
                context_sentence="例文です。",
            )
            for expression, reading in expressions
        ],
    )


def test_format_transcript_labels_roles_and_skips_blanks() -> None:
    transcript = [
        SpeechEvent(role="assistant", text="いらっしゃいませ。"),
        SpeechEvent(role="user", text="  "),
        SpeechEvent(role="user", text="これ ください。"),
    ]

    assert format_transcript(transcript) == (
        "Tutor: いらっしゃいませ。\nLearner: これ ください。"
    )


def test_non_speech_events_become_bracketed_stage_directions() -> None:
    transcript = [
        SpeechEvent(role="assistant", text="ご注文は？"),
        HelpEvent(stage=2, max_stage=4),
        ContextEvent(item=ContextItem(id=1, title="Speisekarte", description="…")),
        SpeechEvent(role="user", text="これください。"),
    ]

    # The brackets keep them out of the dialogue: the analysis is told to quote
    # the learner verbatim, and a line it read as an utterance would come back
    # as a grammar note about something nobody said.
    assert format_transcript(transcript) == (
        "Tutor: ご注文は？\n"
        "[the learner signalled that they were stuck -- help attempt 2 of 4]\n"
        "[the learner was handed: Speisekarte]\n"
        "Learner: これください。"
    )


def test_filter_removes_known_expressions() -> None:
    analysis = make_analysis(("温める", "あたためる"), ("袋", "ふくろ"))

    filtered, removed = filter_known_cards(analysis, {"袋"})

    assert [card.expression for card in filtered.anki_cards] == ["温める"]
    assert removed == ["袋"]


def test_filter_matches_kana_only_vocabulary_by_reading() -> None:
    analysis = make_analysis(("ください", "ください"), ("お箸", "おはし"))

    filtered, removed = filter_known_cards(analysis, {"おはし"})

    assert [card.expression for card in filtered.anki_cards] == ["ください"]
    assert removed == ["お箸"]


def test_filter_deduplicates_repeated_expressions() -> None:
    analysis = make_analysis(("温める", "あたためる"), ("温める", "あたためる"))

    filtered, removed = filter_known_cards(analysis, set())

    assert len(filtered.anki_cards) == 1
    assert removed == []


def test_filter_keeps_summary_and_grammar_notes() -> None:
    analysis = make_analysis(("袋", "ふくろ"))

    filtered, _ = filter_known_cards(analysis, {"袋"})

    assert filtered.summary == "Gut gemacht."
    assert len(filtered.grammar_notes) == 1


def test_strict_schema_marks_every_object_closed_and_required() -> None:
    schema = _strict_schema(SessionAnalysis)

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"summary", "grammar_notes", "anki_cards"}

    card_schema = schema["$defs"]["AnkiCard"]
    assert card_schema["additionalProperties"] is False
    assert set(card_schema["required"]) == {
        "expression",
        "reading",
        "meaning",
        "context_sentence",
    }
