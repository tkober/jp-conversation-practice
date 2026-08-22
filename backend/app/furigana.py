"""Furigana for the transcript.

The Realtime API transcribes into kanji, which is precisely the part a learner
cannot read yet. ``annotate()`` cuts a line into segments and puts a reading on
every kanji run, so the UI can render ``<ruby>`` and hide it again on a toggle.

Readings come from a morphological analysis (MeCab via fugashi, with the
unidic-lite dictionary) rather than from a per-kanji table, because a kanji's
reading depends on the word around it: 今日 is キョウ and not イマヒ, 行った is
イッタ or オコナッタ depending on the verb. The analysis is local -- no API
call, no cost, about 0.1 ms per sentence -- so the relay can annotate every
turn as it arrives.

The dictionary is the one real cost: unidic-lite unpacks to roughly 250 MB in
the image. It is memory-mapped, so the resident footprint stays small.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from .models import RubySegment

logger = logging.getLogger(__name__)

# Katakana and hiragana sit 0x60 apart in Unicode, which is what makes the
# reading (katakana) comparable to the okurigana in the surface (hiragana).
_KANA_DISTANCE = 0x60

_KANA_MARKS = "ーゝゞヽヾ"

# Where unidic's reading is defensible but not what a learner is taught: the
# pronoun 私 is ワタクシ in the dictionary and わたし in every textbook. Keyed by
# the whole surface, so 私立 (シリツ) is unaffected.
_SURFACE_READINGS = {"私": "わたし"}


def _is_kana(char: str) -> bool:
    return "ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヺ" or char in _KANA_MARKS


def _is_kanji(char: str) -> bool:
    # CJK unified ideographs and extension A, plus the repeat mark 々 and 〆.
    return "\u4e00" <= char <= "\u9fff" or "\u3400" <= char <= "\u4dbf" or char in "々〆"


def _to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - _KANA_DISTANCE) if "ァ" <= char <= "ヶ" else char for char in text
    )


def _runs(surface: str) -> list[tuple[str, bool]]:
    """Split a surface into alternating kana / non-kana runs."""
    runs: list[tuple[str, bool]] = []
    for char in surface:
        kana = _is_kana(char)
        if runs and runs[-1][1] == kana:
            runs[-1] = (runs[-1][0] + char, kana)
        else:
            runs.append((char, kana))
    return runs


def _split_word(surface: str, reading: str) -> list[RubySegment]:
    """Distribute a word's reading over its kanji runs: 食べる -> 食[た]べる.

    The okurigana is already readable, so putting the whole reading over the
    whole word (食べる[たべる]) would bury the one part the learner needs. Each
    kana run in the surface has to appear verbatim in the reading; the reading
    between two of them belongs to the kanji run in between.

    Greedy left to right, and anything that does not line up falls back to one
    ruby over the whole word -- still correct, only less precise.
    """
    whole = [RubySegment(text=surface, reading=reading)]
    runs = _runs(surface)
    segments: list[RubySegment] = []
    position = 0

    for index, (run, is_kana) in enumerate(runs):
        if is_kana:
            expected = _to_hiragana(run)
            if not reading.startswith(expected, position):
                return whole
            segments.append(RubySegment(text=run))
            position += len(expected)
            continue

        following = _to_hiragana(runs[index + 1][0]) if index + 1 < len(runs) else ""
        if following:
            # +1: a kanji run reads as at least one kana, so an okurigana match
            # at the current position would be the wrong one.
            end = reading.find(following, position + 1)
        else:
            end = len(reading)
        if end <= position:
            return whole
        segments.append(RubySegment(text=run, reading=reading[position:end]))
        position = end

    if position != len(reading):
        return whole
    return segments


def _merge(segments: list[RubySegment]) -> list[RubySegment]:
    """Glue neighbouring plain segments together to keep the payload small."""
    merged: list[RubySegment] = []
    for segment in segments:
        if segment.reading is None and merged and merged[-1].reading is None:
            merged[-1] = RubySegment(text=merged[-1].text + segment.text)
        else:
            merged.append(segment)
    return merged


@lru_cache(maxsize=1)
def _tagger() -> Any | None:
    """The MeCab tagger, or None if it cannot be loaded.

    A missing or broken dictionary must not take the conversation down with it:
    without a tagger the transcript simply shows no furigana, the same way a
    WaniKani outage degrades to an unfiltered analysis.
    """
    try:
        import fugashi

        return fugashi.Tagger()
    except Exception:  # pragma: no cover - depends on the deployed environment
        logger.warning("Furigana disabled: MeCab/unidic could not be loaded", exc_info=True)
        return None


def annotate(text: str) -> list[RubySegment] | None:
    """Segment one line of Japanese, with a reading on every kanji run.

    Returns None when there is nothing to show -- no tagger, or a line without
    a single kanji -- so the caller can leave the field off entirely and the UI
    renders the plain text it already has.
    """
    tagger = _tagger()
    if tagger is None or not text:
        return None

    segments: list[RubySegment] = []
    for word in tagger(text):
        # MeCab hands whitespace back separately; keeping it means the segments
        # still join up to the original line.
        if word.white_space:
            segments.append(RubySegment(text=word.white_space))
        surface = word.surface
        reading = _SURFACE_READINGS.get(
            surface, _to_hiragana(getattr(word.feature, "kana", None) or "")
        )
        if reading and any(_is_kanji(char) for char in surface):
            segments.extend(_split_word(surface, reading))
        else:
            segments.append(RubySegment(text=surface))

    merged = _merge(segments)
    if not any(segment.reading for segment in merged):
        return None
    return merged
