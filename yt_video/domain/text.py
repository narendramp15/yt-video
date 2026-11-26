"""Slug and scene-marker helpers."""

from __future__ import annotations

import re
from datetime import date

_SCENE_RE = re.compile(r"\[SCENE:([^\]]+)\]")


def make_slug(title: str, today: date) -> str:
    """SEO-friendly filename slug with a YYYYMMDD date suffix."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s]", "", slug)   # strip punctuation
    slug = re.sub(r"[\s_]+", "_", slug)   # whitespace -> underscore
    slug = slug.strip("_")[:60]           # cap length before date
    return f"{slug}_{today.strftime('%Y%m%d')}"


def scene_labels(script: str) -> list[str]:
    """All [SCENE:label] labels in order."""
    return _SCENE_RE.findall(script)


def scene_texts(script: str) -> dict[str, str]:
    """Map each [SCENE:label] to the narration text that follows it."""
    matches = list(_SCENE_RE.finditer(script))
    texts: dict[str, str] = {}
    for index, match in enumerate(matches):
        label = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        texts[label] = script[match.end():end].strip()
    return texts


def strip_scene_markers(script: str) -> str:
    """Remove [SCENE:label] tags and any non-spoken lines so TTS gets pure narration."""
    lines = []
    for line in script.splitlines():
        stripped = line.strip()
        if re.match(r"\[SCENE:[^\]]+\]$", stripped):
            continue
        # Stage directions: lines starting with '('
        if stripped.startswith("("):
            continue
        # Speaker labels: 'Narrator:', 'Host:', 'Speaker:'
        cleaned = re.sub(r"^(?:Narrator|Host|Speaker|Voice|VO)\s*:\s*", "", stripped, flags=re.IGNORECASE)
        # Surrounding quotes that sometimes wrap the spoken line
        cleaned = re.sub(r'^"(.*)"$', r"\1", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def strip_section_markers(script: str) -> str:
    """Back-compat alias of :func:`strip_scene_markers`."""
    return strip_scene_markers(script)
