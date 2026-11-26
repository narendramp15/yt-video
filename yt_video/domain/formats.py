"""Repeatable show templates so the channel feels like a series, not random uploads."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from yt_video.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class FormatSpec:
    id: str
    name: str
    playlist_title: str
    beat_sheet: str
    title_hint: str


_FORMATS: dict[str, FormatSpec] = {
    "trend_vs_reality": FormatSpec(
        id="trend_vs_reality",
        name="Trend vs Reality",
        playlist_title="Trend vs Reality",
        title_hint="Contrast the hype with what the data/facts actually show",
        beat_sheet=(
            "1) Cold open with the viral claim people are sharing.\n"
            "2) Show what is true vs exaggerated (2–3 concrete checks).\n"
            "3) Explain the mechanism behind the mismatch.\n"
            "4) Give the viewer a clear takeaway they can use today.\n"
            "5) Soft CTA asking which claim they believed first."
        ),
    ),
    "what_changed": FormatSpec(
        id="what_changed",
        name="What Changed This Week",
        playlist_title="What Changed This Week",
        title_hint="Week-in-review framing: what shifted and why it matters now",
        beat_sheet=(
            "1) Open with the single biggest change this week.\n"
            "2) Recap 3 beats: before → what flipped → why now.\n"
            "3) Rank who wins / who loses from the change.\n"
            "4) Forecast the next 7 days in one concrete sentence.\n"
            "5) Ask viewers what they are watching next week."
        ),
    ),
    "one_thing": FormatSpec(
        id="one_thing",
        name="One Thing Explained",
        playlist_title="One Thing Explained",
        title_hint="Teach one concept clearly — no survey essay",
        beat_sheet=(
            "1) Promise one idea the viewer will fully understand.\n"
            "2) Define it in plain language with one analogy.\n"
            "3) Walk through one worked example with numbers/names.\n"
            "4) Call out the common mistake people make.\n"
            "5) Close with a 10-second recap + soft CTA."
        ),
    ),
    "numbers": FormatSpec(
        id="numbers",
        name="The Number That Matters",
        playlist_title="The Number That Matters",
        title_hint="Anchor the whole video on one striking number",
        beat_sheet=(
            "1) Lead with the number and why it feels impossible/important.\n"
            "2) Unpack how that number was produced (source / method).\n"
            "3) Compare it to 1–2 reference numbers for scale.\n"
            "4) Translate into a real-world consequence for the viewer.\n"
            "5) End with 'watch this number next' + soft CTA."
        ),
    ),
    "comeback": FormatSpec(
        id="comeback",
        name="Why It Matters Now",
        playlist_title="Why It Matters Now",
        title_hint="Person/story-led urgency: why this moment is different",
        beat_sheet=(
            "1) Open on a human moment or turning point.\n"
            "2) Brief backstory — only what unlocks the present.\n"
            "3) The stake: what changes if this continues.\n"
            "4) Counter-argument or risk people are ignoring.\n"
            "5) Close with a sharp opinion + question for comments."
        ),
    ),
}


def get_format(format_id: str) -> FormatSpec:
    return _FORMATS.get(format_id) or _FORMATS["trend_vs_reality"]


def list_formats() -> list[FormatSpec]:
    return list(_FORMATS.values())


def pick_format(
    topic: str,
    angle: str = "",
    today: datetime.date | None = None,
) -> FormatSpec:
    """Heuristic format picker. ``today`` defaults to the current date (Friday -> weekly recap)."""
    weekday = (today or datetime.date.today()).weekday()

    hay = f"{topic} {angle}".lower()
    if weekday == 4 or "week" in hay or "this week" in hay:
        chosen = _FORMATS["what_changed"]
    elif re.search(r"\d", topic or ""):
        chosen = _FORMATS["numbers"]
    elif any(k in hay for k in ("explain", "how to", "howto", "what is", "guide", "meaning")):
        chosen = _FORMATS["one_thing"]
    elif any(k in hay for k in ("comeback", "return", "career", "player", "celebrity", "story")):
        chosen = _FORMATS["comeback"]
    else:
        chosen = _FORMATS["trend_vs_reality"]

    logger.info(f"Format picked: {chosen.id} ({chosen.name})")
    return chosen


def format_prompt_block(spec: FormatSpec) -> str:
    """Block injected into the mega-script prompt."""
    return (
        f"SHOW FORMAT (follow this structure closely):\n"
        f"  Format ID: {spec.id}\n"
        f"  Format name: {spec.name}\n"
        f"  Title hint: {spec.title_hint}\n"
        f"  Beat sheet:\n{spec.beat_sheet}\n"
        f"Stay inside this format — do not drift into a generic explainer essay.\n"
    )
