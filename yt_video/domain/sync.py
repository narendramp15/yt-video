"""Scene start-time alignment: proportional baseline refined by word timestamps."""

from __future__ import annotations

import re

from yt_video.logging import get_logger

logger = get_logger(__name__)

_ANCHOR_LEN = 6          # narration words used to locate each scene start
_SEARCH_SPAN = 4000      # how far ahead in the transcript to look for a scene


def _scenes_with_text(script: str) -> list[tuple[str, str]]:
    """Ordered [(label, narration_text)] from [SCENE:label] markers."""
    parts = re.split(r"\[SCENE:([^\]]+)\]", script)
    scenes: list[tuple[str, str]] = []
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        scenes.append((label, text))
    return scenes


def _proportional_timeline(script: str, audio_duration: float) -> dict[str, float]:
    """Scene start times proportional to each scene's text length."""
    scenes = [(label, max(len(text), 1)) for label, text in _scenes_with_text(script)]
    if not scenes:
        return {}

    total_chars = sum(n for _, n in scenes)
    timeline: dict[str, float] = {}
    current = 0.0
    for label, chars in scenes:
        timeline[label] = round(current, 2)
        current += (chars / total_chars) * audio_duration

    logger.info("Proportional scene timeline:")
    for label, t in timeline.items():
        logger.info(f"  {label}: {t:.1f}s")
    return timeline


def _norm_token(s: str) -> str:
    """Lowercase and strip everything but letters/digits — for word matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _refine_with_words(
    timeline: dict[str, float],
    words: list[dict],
    script: str,
) -> dict[str, float]:
    """
    Refine the proportional timeline using word timestamps.

    Each scene's first few narration words are located in the transcript; the
    matched word's timestamp becomes the scene start. Scenes are matched in
    order so timing stays monotonic. Scenes that cannot be matched confidently
    keep their proportional value.
    """
    refined = dict(timeline)
    scenes = _scenes_with_text(script)
    if not scenes:
        return refined

    tw: list[tuple[str, float]] = []
    for w in words:
        nt = _norm_token(w["word"])
        if nt:
            tw.append((nt, w["start"]))
    if not tw:
        return refined

    search_from = 0
    prev_start = 0.0

    for label, text in scenes:
        if label not in refined:
            continue

        anchors = [t for t in (_norm_token(x) for x in text.split()) if t][:_ANCHOR_LEN]
        if len(anchors) < 2:
            continue

        # Score each position by how many anchor words appear in order within a short window.
        span = len(anchors) + 4
        limit = min(len(tw), search_from + _SEARCH_SPAN)
        best_idx = -1
        best_hits = 0
        for p in range(search_from, limit):
            window = [tok for tok, _ in tw[p:p + span]]
            hits, wi = 0, 0
            for a in anchors:
                while wi < len(window):
                    if window[wi] == a:
                        hits += 1
                        wi += 1
                        break
                    wi += 1
            if hits > best_hits:
                best_hits, best_idx = hits, p
                if hits == len(anchors):
                    break

        # Require a solid majority of anchor words to match before trusting it.
        if best_idx < 0 or best_hits < max(2, (len(anchors) + 1) // 2):
            logger.debug(f"  {label}: no confident word match — keeping proportional")
            continue

        candidate = round(tw[best_idx][1], 2)
        if candidate < prev_start:
            logger.debug(f"  {label}: word match {candidate:.1f}s precedes previous scene — skipped")
            continue

        logger.debug(f"  {label}: refine {refined[label]:.1f}s -> {candidate:.1f}s")
        refined[label] = candidate
        prev_start = candidate
        search_from = best_idx + 1

    return refined


def align_scenes(words: list[dict], script: str, audio_duration: float) -> dict[str, float]:
    """Map scene label -> start seconds (proportional baseline, refined by ``words``)."""
    timeline = _proportional_timeline(script, audio_duration)
    return _refine_with_words(timeline, words, script)
