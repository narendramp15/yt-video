"""Publish-time helpers: chapters, description, category, privacy, shorts title."""

from __future__ import annotations

import re
from typing import Literal

from yt_video.domain.packaging import humanize_scene_label
from yt_video.logging import get_logger

logger = get_logger(__name__)

# YouTube category IDs:
#   17 Sports · 22 People & Blogs · 24 Entertainment · 25 News & Politics
#   26 Howto & Style · 27 Education · 28 Science & Technology
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("28", ("ai", "chatgpt", "openai", "tech", "software", "startup", "gadget",
            "iphone", "android", "robot", "semiconductor", "chip", "cloud")),
    ("17", ("cricket", "ipl", "football", "soccer", "olympics", "match", "sports",
            "tennis", "nba", "world cup")),
    ("27", ("explained", "how to", "howto", "learn", "guide", "tutorial",
            "history of", "science of")),
    ("26", ("stock", "nifty", "sensex", "ipo", "invest", "mutual fund", "rupee",
            "budget", "rbi", "finance", "market", "trading", "crypto", "bitcoin")),
    ("25", ("news", "election", "government", "policy", "parliament", "modi",
            "breaking")),
    ("24", ("celebrity", "bollywood", "movie", "film", "netflix", "series",
            "viral")),
]


def format_chapter_timestamps(
    timeline: dict[str, float] | None,
    scene_titles: dict[str, str] | None = None,
) -> str:
    """YouTube chapters block. Requires >=3 chapters; first timestamp forced to 0:00."""
    if not timeline:
        return ""

    items = sorted(timeline.items(), key=lambda x: float(x[1]))
    if len(items) < 3:
        logger.debug("Skipping chapters — need at least 3 timeline entries")
        return ""

    lines = ["Chapters:"]
    for index, (label, start) in enumerate(items):
        seconds = 0.0 if index == 0 else max(float(start), 0.0)
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        title = (scene_titles or {}).get(label) or humanize_scene_label(label)
        lines.append(f"{mins}:{secs:02d} {title}")

    return "\n".join(lines)


def build_publish_description(
    base_description: str,
    timeline: dict[str, float] | None = None,
    scene_titles: dict[str, str] | None = None,
) -> str:
    """Merge SEO blurb with auto-generated chapter timestamps."""
    base = (base_description or "").strip()
    chapters = format_chapter_timestamps(timeline, scene_titles)
    if not chapters:
        return base
    if not base:
        return chapters
    return f"{base}\n\n{chapters}"


def _keyword_matches(haystack: str, keyword: str) -> bool:
    """Single tokens use word boundaries ("ai" must not match "daily"); phrases are substrings."""
    kw = (keyword or "").strip().lower()
    if not kw:
        return False
    if " " in kw:
        return kw in haystack
    return re.search(rf"\b{re.escape(kw)}\b", haystack) is not None


def resolve_category_id(topic: str, title: str = "", default: str = "22") -> str:
    """Map topic/title keywords to a YouTube categoryId (People & Blogs by default)."""
    haystack = f"{topic} {title}".lower()
    for category_id, keywords in _CATEGORY_RULES:
        if any(_keyword_matches(haystack, kw) for kw in keywords):
            logger.info(f"Category mapped -> {category_id} (topic match)")
            return category_id
    logger.info(f"Category fallback -> {default}")
    return default or "22"


def decide_privacy(
    score: int,
    safety: str | None,
    threshold: int = 80,
) -> Literal["public", "private"]:
    """Public only when score >= threshold AND safety is "Yes"; anything else is private."""
    safe = (safety or "").strip().lower() == "yes"
    return "public" if score >= threshold and safe else "private"


def build_up_next_blurb(recent_video_ids: list[str] | None = None) -> str:
    """Description / pinned-comment blurb linking recent videos as 'Watch next'."""
    ids = [v for v in (recent_video_ids or []) if v]
    if not ids:
        return ""
    lines = ["Watch next:"]
    for i, vid in enumerate(ids[:3], 1):
        lines.append(f"{i}. https://www.youtube.com/watch?v={vid}")
    return "\n".join(lines)


def shorts_title(publish_title: str) -> str:
    """Append ``#Shorts`` (once) to the long-form title."""
    if "#shorts" in publish_title.lower():
        return publish_title
    return f"{publish_title[:60].rstrip()} #Shorts"
