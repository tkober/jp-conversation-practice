"""Semantic VAD tuning: how long a pause may last before the tutor answers.

``eagerness`` is the Realtime API's knob for when silence counts as the end of
the learner's turn. The default is ``low``, the most patient setting, because a
learner assembling a sentence pauses where a native speaker would not, and
being cut off mid-thought is about the most discouraging thing the tutor can
do.

It is configurable because the right value is personal and changes as the
learner improves: someone who no longer needs that much room spends every turn
waiting for a tutor that already knows they have finished. The Settings screen
sets the default, the session screen changes it live -- the frustration shows
up during a conversation, not before it.
"""

from __future__ import annotations

from typing import Any

# OpenAI's values. Ordered from most to least patient; "auto" lets the model
# decide, which is the API's own default and a fine choice for a fluent user.
EAGERNESS_LEVELS = ("low", "medium", "high", "auto")

DEFAULT_EAGERNESS = "low"


def is_valid_eagerness(value: str) -> bool:
    return value in EAGERNESS_LEVELS


def normalise_eagerness(value: Any, fallback: str = DEFAULT_EAGERNESS) -> str:
    """Coerce an untrusted value into a supported level.

    Falls back rather than raising: an unknown value from the browser or from a
    hand-edited settings row must not be able to end a session.
    """
    candidate = str(value or "").strip().lower()
    if is_valid_eagerness(candidate):
        return candidate
    return fallback if is_valid_eagerness(fallback) else DEFAULT_EAGERNESS
