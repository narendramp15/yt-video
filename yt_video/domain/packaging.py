"""Title / thumbnail packaging helpers: hooks, scene titles, heuristic scoring."""

from __future__ import annotations

import re

from yt_video.logging import get_logger
from yt_video.models import PackagingVariant, ScoredVariant

logger = get_logger(__name__)

_CLICKBAIT_SPAM = (
    "you won't believe", "gone wrong", "shocking", "must watch",
    "click here", "number 7 will", "doctors hate",
)


def derive_thumbnail_hook(title: str, max_words: int = 5) -> str:
    """Build a 3-5 word mobile-readable hook from the SEO title (thumbnail text)."""
    if not title:
        return "WATCH THIS"
    # Prefer the punchiest clause before a separator
    head = re.split(r"[|–—:\-]", title, maxsplit=1)[0].strip()
    words = re.findall(r"[A-Za-z0-9']+", head)
    if not words:
        return "WATCH THIS"
    return " ".join(words[:max_words]).upper()


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


def score_packaging_variant(v: PackagingVariant) -> float:
    """Heuristic CTR score for a title/hook package. Higher is better."""
    title = (v.title or "").strip()
    hook = (v.hook or "").strip()
    if not title:
        return -100.0

    score = 0.0
    tlen = len(title)
    # Sweet spot ~40-65 chars for mobile title truncation
    if 40 <= tlen <= 65:
        score += 3.0
    elif 30 <= tlen < 40 or 65 < tlen <= 75:
        score += 1.5
    else:
        score -= 1.0

    if re.search(r"\d", title):
        score += 1.5
    if re.search(r"[A-Z][a-z]+", title):  # proper-noun-ish casing present
        score += 0.5
    if "?" in title:
        score += 0.75

    low = title.lower()
    if any(spam in low for spam in _CLICKBAIT_SPAM):
        score -= 4.0

    hook_words = re.findall(r"[A-Za-z0-9']+", hook)
    if 3 <= len(hook_words) <= 5:
        score += 2.0
    elif hook_words:
        score += 0.5
    else:
        score -= 1.0

    return round(score, 2)


def pick_best_packaging(variants: list[PackagingVariant]) -> ScoredVariant:
    """Return the highest-scoring variant (keeps input order on ties)."""
    if not variants:
        return ScoredVariant(variant_id="fallback", title="", hook="WATCH THIS", score=0.0)
    best = max(
        enumerate(variants),
        key=lambda pair: (score_packaging_variant(pair[1]), -pair[0]),
    )[1]
    fields = {k: getattr(best, k) for k in PackagingVariant.model_fields}
    return ScoredVariant(**fields, score=score_packaging_variant(best))


def fallback_variants(base_title: str, topic: str, n: int) -> list[PackagingVariant]:
    """Deterministic variants so the pipeline never stalls when the LLM fails."""
    base = (base_title or topic or "Today's Trend").strip()
    count = max(1, min(int(n), 5))
    hook = derive_thumbnail_hook(base)
    variants = [
        PackagingVariant(variant_id="v1", title=base[:70], hook=hook),
        PackagingVariant(
            variant_id="v2",
            title=(f"{base.rstrip('.')} — Explained")[:70],
            hook=hook,
        ),
        PackagingVariant(
            variant_id="v3",
            title=(f"What Changed: {topic}")[:70],
            hook=derive_thumbnail_hook(topic or base),
        ),
    ]
    return variants[:count]
