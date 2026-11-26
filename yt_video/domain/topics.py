"""Topic re-ranking and the niche hard filter."""

from __future__ import annotations

import re

from yt_video.logging import get_logger
from yt_video.models import Topic

logger = get_logger(__name__)

_STOP = {
    "this", "that", "with", "from", "about", "what", "will", "have",
    "your", "their", "when", "which", "into", "over", "after", "before",
    "today", "week", "india", "indian", "video", "news",
    "an", "as", "at", "be", "by", "do", "if", "in", "is", "it", "no",
    "of", "on", "or", "so", "to", "up", "we",
}


def tokenize(text: str) -> set[str]:
    # Keep 2+ char tokens so niche keywords like "ai" / "ipl" survive.
    return {w for w in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if w not in _STOP}


def niche_lock_prompt_note(keywords: list[str], days: int) -> str:
    """Prompt note injected into topic + script prompts when a niche lock is active."""
    if not keywords:
        return ""
    kw = ", ".join(keywords)
    return (
        f"NICHE LOCK ({days}-day primary niche): stay inside [{kw}]. "
        f"Frame every topic/angle through this niche. Drop off-niche digressions.\n"
    )


def apply_niche_hard_filter(ranked: list[Topic], keywords: list[str]) -> list[Topic]:
    """
    Hard niche lock: drop topics with zero token overlap against ``keywords``.

    If every topic is filtered out, keep the top-scored item and force its
    angle toward the niche. Empty ``keywords`` skips the filter.
    """
    if not keywords:
        logger.warning("Niche keywords empty — niche hard lock skipped")
        return ranked

    niche_set: set[str] = set()
    for kw in keywords:
        niche_set |= tokenize(kw)
    if not niche_set:
        return ranked

    kept = [t for t in ranked if tokenize(f"{t.title} {t.angle}") & niche_set]
    if kept:
        logger.info(
            f"Niche hard filter: kept {len(kept)}/{len(ranked)} topics "
            f"(niche={','.join(keywords)})"
        )
        return kept

    if not ranked:
        return ranked
    best = max(ranked, key=lambda t: float(t.score or 0))
    forced = (
        f"Frame through {', '.join(keywords)}: "
        f"{best.angle or best.title or 'explain why it matters'}"
    )
    logger.warning(
        f"Niche hard filter removed all topics — keeping '{best.title}' "
        f"with forced niche angle"
    )
    return [best.model_copy(update={"angle": forced})]


def rerank_topics(
    ranked: list[Topic],
    recent_titles: list[str],
    niche_keywords: list[str],
    top_keywords: list[str],
    raw_signals: list[str] | None = None,
    top_n: int = 3,
) -> list[Topic]:
    """
    Blend LLM score with deterministic novelty / velocity / niche adjustments.

    ``niche_keywords`` (configured niche) win; ``top_keywords`` (analytics) are
    the fallback when no niche is configured.
    """
    if not ranked:
        return []

    signals = raw_signals or []
    recent_tokens = [tokenize(t) for t in recent_titles if t]
    niche: set[str] = set()
    for kw in (niche_keywords or top_keywords or []):
        niche |= tokenize(kw)

    adjusted: list[tuple[Topic, float]] = []
    for item in ranked:
        tokens = tokenize(f"{item.title} {item.angle}")
        delta = 0.0

        # Novelty: penalize overlap with recently posted topics
        if recent_tokens and tokens:
            best_overlap = max(
                (len(tokens & rt) / max(len(tokens), 1) for rt in recent_tokens),
                default=0.0,
            )
            if best_overlap >= 0.5:
                delta -= 2.5
            elif best_overlap >= 0.3:
                delta -= 1.25

        # Velocity: bonus if topic tokens appear across multiple raw signals
        if signals and tokens:
            hits = sum(1 for s in signals if tokens & tokenize(s))
            if hits >= 3:
                delta += 1.5
            elif hits >= 2:
                delta += 0.75

        # Niche fit
        if niche and tokens & niche:
            delta += 1.25

        row = item.model_copy(update={"score": round(float(item.score or 0.0) + delta, 2)})
        adjusted.append((row, round(delta, 2)))

    adjusted.sort(key=lambda pair: pair[0].score, reverse=True)
    out = adjusted[:top_n]
    logger.info(
        f"Topic re-rank: top='{out[0][0].title}' score={out[0][0].score} (delta={out[0][1]})"
    )
    return [row for row, _ in out]
