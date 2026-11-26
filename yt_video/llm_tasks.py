"""Typed LLM tasks: each renders a prompt template and calls the router."""
from __future__ import annotations

from yt_video.domain.packaging import fallback_variants
from yt_video.logging import get_logger
from yt_video.models import (
    ContentAngle,
    PackagingVariant,
    PackagingVariants,
    RankedTopics,
    Review,
    ScenePromptList,
    ScriptOutput,
    Topic,
)
from yt_video.prompts import render_prompt
from yt_video.providers.llm.base import LLMError, LLMRequest
from yt_video.providers.llm.router import LLMRouter

log = get_logger(__name__)


def rank_topics(
    router: LLMRouter,
    signals: list[str],
    recent_titles: list[str],
    niche_note: str,
    analytics_note: str,
    top_n: int = 3,
) -> list[Topic]:
    """Filter sensitive topics and score the rest; may return an empty list."""
    shown = signals[:30]
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(shown))
    if recent_titles:
        numbered += "\n\nRECENT VIDEO TITLES (avoid repeating these):\n" + "\n".join(
            f"- {t}" for t in recent_titles
        )
    system, prompt = render_prompt(
        "rank_topics",
        count=str(len(shown)),
        top_n=str(top_n),
        topics=numbered,
        niche_note=niche_note,
        analytics_note=analytics_note,
    )
    req = LLMRequest("rank_topics", system, prompt, RankedTopics, "light")
    result = router.complete(req, stage="rank_topics")
    assert isinstance(result.parsed, RankedTopics)
    return result.parsed.topics


def write_script(
    router: LLMRouter,
    topic: str,
    angle: str,
    trend_signals: list[str],
    format_block: str,
    tts_note: str,
    analytics_note: str,
) -> ScriptOutput:
    trend_block = ""
    if trend_signals:
        bullets = "\n".join(f"  • {t}" for t in trend_signals[:5])
        trend_block = (
            "\nRELATED TRENDING SIGNALS (weave 1-2 naturally where relevant):\n" + bullets + "\n"
        )
    system, prompt = render_prompt(
        "write_script",
        topic=topic,
        angle_block=f"\nSUGGESTED ANGLE: {angle}\n" if angle else "",
        trend_block=trend_block,
        format_block=f"\n{format_block}\n" if format_block else "",
        analytics_note=analytics_note,
        tts_note=tts_note,
    )
    req = LLMRequest("write_script", system, prompt, ScriptOutput, "script")
    result = router.complete(req, stage="write_script")
    assert isinstance(result.parsed, ScriptOutput)
    return result.parsed


def packaging_variants(
    router: LLMRouter, topic: str, base_title: str, angle: str, n: int
) -> list[PackagingVariant]:
    """Title/hook variants; deterministic fallback when every provider fails."""
    count = max(1, min(int(n), 5))
    base = (base_title or topic or "Today's Trend").strip()
    system, prompt = render_prompt(
        "packaging_variants",
        topic=topic,
        base_title=base,
        angle_line=f"ANGLE: {angle}\n" if angle else "",
        n=str(count),
    )
    req = LLMRequest("packaging_variants", system, prompt, PackagingVariants, "light")
    try:
        result = router.complete(req, stage="packaging")
    except LLMError as exc:
        log.warning("packaging variants LLM failed (%s); using fallbacks", exc)
        return fallback_variants(base_title, topic, n)
    assert isinstance(result.parsed, PackagingVariants)
    return result.parsed.variants


def review(router: LLMRouter, long_script: str) -> Review:
    system, prompt = render_prompt("review", script=long_script)
    req = LLMRequest("review", system, prompt, Review, "light")
    result = router.complete(req, stage="review")
    assert isinstance(result.parsed, Review)
    return result.parsed


def refine_scene_prompts(
    router: LLMRouter,
    topic: str,
    angle: str | None,
    labels: list[str],
    scene_texts: dict[str, str],
    initial: dict[str, str],
) -> dict[str, str]:
    """Rewrite scene briefs into tight image prompts; ``initial`` on failure."""
    if not labels:
        return {}
    lines: list[str] = []
    for label in labels:
        narration = (scene_texts.get(label) or "").strip()
        existing = (initial.get(label) or "").strip()
        line = f"- {label}:"
        if existing:
            line += f"\n    initial: {existing[:200]}"
        if narration:
            line += f"\n    narration: {narration[:300]}"
        lines.append(line)
    system, prompt = render_prompt(
        "refine_scene_prompts",
        topic=topic,
        angle_line=f"ANGLE: {angle}\n" if angle else "",
        scenes_text="\n".join(lines),
    )
    req = LLMRequest("refine_scene_prompts", system, prompt, ScenePromptList, "script")
    try:
        result = router.complete(req, stage="images")
    except LLMError as exc:
        log.warning("refine_scene_prompts failed (%s); using initial prompts", exc)
        return dict(initial)
    assert isinstance(result.parsed, ScenePromptList)
    wanted = set(labels)
    refined = {sp.label: sp.prompt for sp in result.parsed.scene_prompts if sp.label in wanted and sp.prompt}
    for label in labels:
        if label not in refined and initial.get(label):
            refined[label] = initial[label]
    return refined


def content_angle(router: LLMRouter, topic: str) -> str:
    system, prompt = render_prompt("content_angle", topic=topic)
    req = LLMRequest("content_angle", system, prompt, ContentAngle, "light")
    result = router.complete(req, stage="rank_topics")
    assert isinstance(result.parsed, ContentAngle)
    return result.parsed.angle
