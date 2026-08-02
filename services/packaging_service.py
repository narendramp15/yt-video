"""
packaging_service.py — click-auction helpers for Phase A.

Owns title/thumb text, description chapters, category mapping, and
human-readable scene titles used in overlays and YouTube chapters.
"""

from __future__ import annotations

import re
from logger import logger

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


def derive_thumbnail_hook(title: str, max_words: int = 5) -> str:
    """
    Build a 3–5 word mobile-readable hook from the SEO title.
    Used as on-image thumbnail text (Pillow overlay).
    """
    if not title:
        return "WATCH THIS"
    # Prefer the punchiest clause before a separator
    head = re.split(r"[|–—:\-]", title, maxsplit=1)[0].strip()
    words = re.findall(r"[A-Za-z0-9']+", head)
    if not words:
        return "WATCH THIS"
    hook = " ".join(words[:max_words])
    return hook.upper()


def humanize_scene_label(label: str, scene_text: str = "") -> str:
    """Turn s1/intro markers into viewer-facing chapter/overlay titles."""
    label = (label or "").strip()
    if scene_text:
        first = scene_text.strip().split(".")[0].strip()
        words = re.findall(r"[A-Za-z0-9']+", first)
        if len(words) >= 3:
            phrase = " ".join(words[:6])
            return phrase[:48].rstrip()

    special = {"intro": "Introduction", "outro": "Key Takeaway"}
    if label.lower() in special:
        return special[label.lower()]

    if re.fullmatch(r"s\d+", label, flags=re.IGNORECASE):
        return f"Part {label[1:]}"

    return label.replace("_", " ").replace("-", " ").title()


def scene_titles_from_script(
    labels: list[str],
    scene_texts: dict[str, str] | None = None,
) -> dict[str, str]:
    texts = scene_texts or {}
    return {label: humanize_scene_label(label, texts.get(label, "")) for label in labels}


def format_chapter_timestamps(
    timeline: dict[str, float] | None,
    scene_titles: dict[str, str] | None = None,
) -> str:
    """
    YouTube chapters block for the description.
    Requires ≥3 chapters; first timestamp forced to 0:00.
    """
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


def resolve_category_id(
    topic: str,
    title: str = "",
    default: str = "22",
) -> str:
    """
    Map topic/title keywords → YouTube categoryId.
    Defaults to People & Blogs (22) when no rule matches — safer than
    always forcing News & Politics.
    """
    haystack = f"{topic} {title}".lower()
    for category_id, keywords in _CATEGORY_RULES:
        if any(_keyword_matches(haystack, kw) for kw in keywords):
            logger.info(f"Category mapped -> {category_id} (topic match)")
            return category_id
    logger.info(f"Category fallback -> {default}")
    return default or "22"


def _keyword_matches(haystack: str, keyword: str) -> bool:
    """
    Match keywords without false positives like "ai" inside "daily".
    Multi-word phrases stay substring matches; single tokens use word boundaries.
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return False
    if " " in kw:
        return kw in haystack
    return re.search(rf"\b{re.escape(kw)}\b", haystack) is not None


def decide_privacy(score: int, safety: str, threshold: int = 80) -> str:
    """Public only when quality score clears threshold AND safety is Yes."""
    safe = (safety or "").strip().lower() == "yes"
    if score >= threshold and safe:
        return "public"
    return "private"
