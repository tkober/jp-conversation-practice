"""Loading the built-in scenarios from Markdown files.

Scenarios ship as Markdown with YAML front matter so they can be edited,
reviewed and diffed as prose rather than buried in a Python list. They are the
*initial* content only: once seeded, the database is the source of truth, and
the Settings/Editor screens work against that.

    ---
    slug: konbini
    title: Einkaufen im Kombini
    summary: Abendschicht an der Kasse ...
    ---

    You are the clerk at a Japanese convenience store ...

The body is the model-facing prompt and is therefore English; `title` and
`summary` are user-facing and therefore German.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .config import get_settings

log = logging.getLogger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class ScenarioFile:
    slug: str
    title: str
    summary: str
    prompt: str


def scenarios_dir() -> Path:
    """Where the Markdown scenarios live (overridable for the container)."""
    configured = get_settings().scenarios_dir
    path = Path(configured)
    if path.is_absolute():
        return path
    # Relative paths resolve against the backend package's parent, so running
    # from backend/ and from / (the image's workdir) both work.
    return Path(__file__).resolve().parent.parent / path


def load_scenario_files() -> list[ScenarioFile]:
    """Parse every ``*.md`` in the scenarios directory, sorted by filename.

    Filenames carry a numeric prefix (``01-konbini.md``) purely to fix the
    order in the picker; the slug comes from the front matter.

    A malformed file is skipped with a log line rather than raising: one bad
    file must not stop the app from booting.
    """
    directory = scenarios_dir()
    if not directory.is_dir():
        log.warning("Scenarios directory %s does not exist", directory)
        return []

    scenarios: list[ScenarioFile] = []
    seen: set[str] = set()

    for path in sorted(directory.glob("*.md")):
        try:
            parsed = frontmatter.load(path)
        except Exception as exc:  # noqa: BLE001 - malformed YAML raises broadly
            log.warning("Skipping scenario %s: %s", path.name, exc)
            continue

        slug = str(parsed.get("slug") or path.stem.split("-", 1)[-1]).strip()
        title = str(parsed.get("title") or "").strip()
        summary = str(parsed.get("summary") or "").strip()
        prompt = parsed.content.strip()

        if not SLUG_PATTERN.match(slug):
            log.warning("Skipping scenario %s: invalid slug %r", path.name, slug)
            continue
        if not title or not prompt:
            log.warning("Skipping scenario %s: title and body are required", path.name)
            continue
        if slug in seen:
            log.warning("Skipping scenario %s: duplicate slug %r", path.name, slug)
            continue

        seen.add(slug)
        scenarios.append(ScenarioFile(slug=slug, title=title, summary=summary, prompt=prompt))

    return scenarios
