"""Furigana alignment.

The interesting part is not "is there a reading" but *where* it lands: the
reading belongs over the kanji, not over the okurigana the learner can already
read. These cases are the ones that broke while the alignment was written.
"""

from __future__ import annotations

import pytest

from app.furigana import annotate


def rendered(text: str) -> str:
    """The annotation as ``kanji[reading]``, which is easy to read in a diff."""
    segments = annotate(text)
    assert segments is not None
    return "".join(
        f"{segment.text}[{segment.reading}]" if segment.reading else segment.text
        for segment in segments
    )


def test_segments_join_back_to_the_original_line():
    line = "先生、今日は3時に会いましょう。"
    segments = annotate(line)
    assert segments is not None
    assert "".join(segment.text for segment in segments) == line


def test_reading_covers_the_kanji_only():
    assert rendered("食べる") == "食[た]べる"
    assert rendered("忙しい") == "忙[いそが]しい"


def test_okurigana_between_two_kanji_stays_bare():
    assert rendered("落ち着く") == "落[お]ち着[つ]く"


def test_compound_gets_one_reading_per_word():
    assert rendered("東京駅") == "東京[とうきょう]駅[えき]"


def test_reading_follows_the_word_not_the_character():
    # 今日 is キョウ here; a per-kanji table would read it イマ + ヒ.
    assert rendered("今日はいい天気ですね") == "今日[きょう]はいい天気[てんき]ですね"


def test_katakana_and_digits_are_left_alone():
    assert rendered("コーヒーを二つください") == "コーヒーを二[ふた]つください"
    assert rendered("3時に会いましょう") == "3時[じ]に会[あ]いましょう"


@pytest.mark.parametrize("line", ["", "ひらがなだけ", "コーヒー", "OK!"])
def test_lines_without_kanji_are_not_annotated(line: str):
    assert annotate(line) is None


def test_the_learner_reading_wins_over_the_dictionary_default():
    # unidic reads the pronoun as ワタクシ, which is not what a learner is
    # taught first -- and the point of the annotation is what to say.
    assert rendered("私は学生です") == "私[わたし]は学生[がくせい]です"


def test_plain_runs_are_merged_into_one_segment():
    segments = annotate("私は東京に住んでいます")
    assert segments is not None
    # No two adjacent segments may both be plain -- that would bloat the payload.
    plain = [segment.reading is None for segment in segments]
    assert not any(first and second for first, second in zip(plain, plain[1:]))
