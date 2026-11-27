"""Text stages 1-6: discover, rank, format, script, packaging, review.

Ported from v1 ``workflows/long_video.py:151-289``. Each stage stores
JSON-serializable data under its own name in ``run.json``.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from yt_video import llm_tasks
from yt_video.domain.formats import format_prompt_block, get_format, pick_format
from yt_video.domain.packaging import derive_thumbnail_hook, pick_best_packaging
from yt_video.domain.text import make_slug, strip_scene_markers
from yt_video.domain.topics import (
    apply_niche_hard_filter,
    content_angle,
    niche_lock_prompt_note,
    rerank_topics,
)
from yt_video.integrations.trends import fetch_trending_topics
from yt_video.logging import get_logger
from yt_video.memory._io import read_json, write_json_atomic
from yt_video.models import PackagingVariant, Review, ScoredVariant, ScriptOutput, Topic
from yt_video.pipeline.context import RunContext
from yt_video.pipeline.runner import Stage, StageOutcome
from yt_video.providers.llm.base import LLMError

log = get_logger(__name__)

TOP_N = 3
PLACEHOLDER_TOPIC = Topic(title="Today's Trending Topic", angle="", audience="", score=0)
PENDING_FILE = "packaging_pending.json"


class RankData(BaseModel):
    topics: list[Topic]
    selected: Topic


class PackagingData(BaseModel):
    variants: list[PackagingVariant]
    winner: ScoredVariant
    publish_title: str
    thumb_hook: str
    slug: str


def _niche_note(ctx: RunContext) -> str:
    s = ctx.settings
    if s.niche_lock_enabled and s.channel_primary_niche:
        return niche_lock_prompt_note(s.channel_primary_niche, s.niche_lock_days)
    return ""


def _fallback_pool(raw_signals: list[str]) -> list[Topic]:
    """v1 fallback: raw signals as zero-score topics (pool of ``top_n * 3``)."""
    return [
        Topic(title=t, angle="", audience="general", score=0.0)
        for t in raw_signals[: max(TOP_N * 3, TOP_N)]
    ]


class DiscoverStage:
    name = "discover"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        raw_signals = fetch_trending_topics(ctx.settings)
        log.info("raw signals fetched: %d", len(raw_signals))
        ctx.run.mark_done(self.name, {"raw_signals": raw_signals})
        return None


class RankTopicsStage:
    name = "rank_topics"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        s = ctx.settings
        raw_signals: list[str] = ctx.run.get("discover").get("raw_signals", [])
        recent = ctx.analytics.recent_video_topics()
        ranked: list[Topic] = []
        if raw_signals:
            try:
                ranked = llm_tasks.rank_topics(
                    ctx.router,
                    raw_signals,
                    recent,
                    _niche_note(ctx),
                    ctx.analytics.topic_prompt_injection(),
                    top_n=TOP_N,
                )
            except LLMError as exc:
                log.warning("AI topic filter failed (%s); using raw topic list as fallback", exc)
            if not ranked:
                ranked = _fallback_pool(raw_signals)
        if s.niche_lock_enabled:
            ranked = apply_niche_hard_filter(ranked, s.channel_primary_niche)
        topics = rerank_topics(
            ranked,
            recent,
            s.channel_primary_niche,
            ctx.analytics.top_topic_keywords(),
            raw_signals=raw_signals,
            top_n=TOP_N,
        )[:TOP_N]
        selected = topics[0] if topics else PLACEHOLDER_TOPIC
        if not selected.angle:
            selected = selected.model_copy(
                update={"angle": content_angle(selected.title, ctx.today)}
            )
        log.info("selected topic: %r angle: %r", selected.title, selected.angle)
        ctx.run.mark_done(
            self.name,
            {"topics": [t.model_dump() for t in topics], "selected": selected.model_dump()},
        )
        return None


class PickFormatStage:
    name = "pick_format"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        sel = ctx.load("rank_topics", RankData).selected
        spec = pick_format(sel.title, angle=sel.angle, today=ctx.today)
        ctx.run.mark_done(
            self.name,
            {
                "format_id": spec.id,
                "format_name": spec.name,
                "playlist_title": spec.playlist_title,
            },
        )
        return None


class WriteScriptStage:
    name = "write_script"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        rank = ctx.load("rank_topics", RankData)
        raw_signals: list[str] = ctx.run.get("discover").get("raw_signals", [])
        spec = get_format(ctx.run.get("pick_format")["format_id"])
        supporting = [t.title for t in rank.topics[1:]] + raw_signals[:5]
        script = llm_tasks.write_script(
            ctx.router,
            rank.selected.title,
            rank.selected.angle,
            supporting,
            format_prompt_block(spec),
            ctx.tts_memory.prompt_injection(),
            ctx.analytics.script_prompt_injection(),
            niche_note=_niche_note(ctx),
        )
        log.info(
            "script: long=%d chars shorts=%d chars",
            len(script.long_script),
            len(script.shorts_script),
        )
        ctx.run.mark_done(self.name, script.model_dump())
        return None


class PackagingStage:
    name = "packaging"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        sel = ctx.load("rank_topics", RankData).selected
        topic, angle = sel.title, sel.angle
        script = ctx.load("write_script", ScriptOutput)
        variants = llm_tasks.packaging_variants(
            ctx.router, topic, script.seo_title or topic, angle, ctx.settings.packaging_variants
        )
        winner = pick_best_packaging(variants)
        publish_title = (winner.title or script.seo_title or topic).strip()
        thumb_hook = (winner.hook or derive_thumbnail_hook(publish_title)).strip()
        slug = make_slug(publish_title or topic, ctx.today)
        ctx.run.set_slug(slug)
        log.info(
            "packaging winner %s %r hook=%r slug=%s",
            winner.variant_id, winner.title, winner.hook, slug,
        )

        variant_dumps = [v.model_dump() for v in variants]
        winner_dump = winner.model_dump()
        if ctx.settings.packaging_require_approval:
            self._write_pending(ctx, topic, angle, variant_dumps, winner_dump, slug)
        ctx.run.mark_done(
            self.name,
            {
                "variants": variant_dumps,
                "winner": winner_dump,
                "publish_title": publish_title,
                "thumb_hook": thumb_hook,
                "slug": slug,
            },
        )
        return None

    @staticmethod
    def _write_pending(
        ctx: RunContext, topic: str, angle: str, variants: list[dict], winner: dict, slug: str
    ) -> None:
        path = ctx.run.path(PENDING_FILE)
        existing = read_json(path) or {}
        if existing.get("approved") and existing.get("topic") == topic:
            return  # do not clobber an already-approved gate on resume
        write_json_atomic(
            path,
            {
                "topic": topic,
                "angle": angle,
                "variants": variants,
                "winner": winner,
                "slug": slug,
                "format_id": ctx.run.get("pick_format").get("format_id", ""),
                "approved": False,
                "rejected": False,
                "chosen_index": None,
            },
            ensure_ascii=False,
        )
        log.info("packaging pending written -> %s", path)


class ReviewStage:
    name = "review"

    def run(self, ctx: RunContext) -> StageOutcome | None:
        script = ctx.load("write_script", ScriptOutput)
        result = llm_tasks.review(ctx.router, strip_scene_markers(script.long_script))
        log.info("review score: %d/100 safety=%s", result.score, result.safety)
        topic = ctx.load("rank_topics", RankData).selected.title
        self._append_log(ctx, topic, result)
        ctx.run.mark_done(self.name, result.model_dump())
        return None


    @staticmethod
    def _append_log(ctx: RunContext, topic: str, result: Review) -> None:
        """Append one NDJSON line to state/review_log.json (v1 format); non-fatal."""
        line = {
            "topic": topic,
            "score": result.score,
            "review": result.notes,
            "safety": result.safety,
        }
        try:
            path = ctx.settings.state_dir / "review_log.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("review log write failed (non-fatal): %s", exc)


TEXT_STAGES: list[type[Stage]] = [
    DiscoverStage,
    RankTopicsStage,
    PickFormatStage,
    WriteScriptStage,
    PackagingStage,
    ReviewStage,
]
