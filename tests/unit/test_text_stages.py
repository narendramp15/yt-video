"""Tests for the text stages (discover -> review) using fakes only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from tests.unit.test_models import make_script
from yt_video.memory._io import read_json, write_json_atomic
from yt_video.memory.analytics_memory import AnalyticsMemory
from yt_video.memory.tts_memory import TTSMemory
from yt_video.pipeline.context import RunContext
from yt_video.pipeline.run_store import RunStore
from yt_video.pipeline.runner import Pipeline
from yt_video.pipeline.stages import text
from yt_video.pipeline.stages.text import TEXT_STAGES
from yt_video.providers.llm.base import ProviderError
from yt_video.providers.llm.fake import FakeLLM
from yt_video.providers.llm.router import LLMRouter

TODAY = date(2026, 11, 26)  # Thursday
SIGNALS = ["AI chip boom", "Cricket final", "Budget 2027", "Monsoon update"]
TOPICS = [
    {"title": "AI chip boom", "angle": "why it matters", "audience": "tech", "score": 9.0},
    {"title": "Cricket final", "angle": "", "audience": "fans", "score": 7.0},
]
REVIEW = {"score": 88, "safety": "Yes", "notes": "good"}
VARIANTS = {
    "variants": [
        {"variant_id": "v1", "title": "Why the AI chip boom changes 2027 forever", "hook": "CHIPS ARE EVERYTHING"},
        {"variant_id": "v2", "title": "chips", "hook": ""},
    ]
}


@dataclass
class Env:
    ctx: RunContext
    fake: FakeLLM
    store: RunStore


def _make_env(tmp_path, settings, monkeypatch, responses: dict, **overrides: Any) -> Env:
    for k, v in overrides.items():
        setattr(settings, k, v)
    monkeypatch.setattr(text, "fetch_trending_topics", lambda s: list(SIGNALS))
    store = RunStore(tmp_path / "runs")
    run = store.create(datetime(2026, 11, 26, 9, 0, 0))
    fake = FakeLLM(responses)
    none: Any = None
    ctx = RunContext(
        settings=settings,
        run=run,
        router=LLMRouter([fake], None, run.id),
        images=none,
        clip=none,
        tts_memory=TTSMemory(tmp_path / "tts.json"),
        analytics=AnalyticsMemory(tmp_path / "an.json", tmp_path / "an.log", None),
        youtube=None,
        ledger=none,
        no_upload=True,
        today=TODAY,
    )
    return Env(ctx, fake, store)


def _responses(**over: Any) -> dict:
    base = {
        "rank_topics": {"topics": TOPICS},
        "write_script": make_script(),
        "packaging_variants": VARIANTS,
        "review": REVIEW,
    }
    base.update(over)
    return base


@pytest.fixture
def env(tmp_path, settings, monkeypatch) -> Env:
    return _make_env(tmp_path, settings, monkeypatch, _responses(), packaging_require_approval=True)


def _run_all(ctx: RunContext) -> str:
    return Pipeline([cls() for cls in TEXT_STAGES]).run(ctx)


def test_text_stages_end_to_end_with_fakes(env):
    assert _run_all(env.ctx) == "done"
    run = env.ctx.run
    for stage in ("discover", "rank_topics", "pick_format", "write_script", "packaging", "review"):
        assert run.is_done(stage), stage
    assert run.get("discover") == {"raw_signals": SIGNALS}
    sel = run.get("rank_topics")["selected"]
    assert sel["title"] == "AI chip boom" and sel["angle"]
    assert run.get("pick_format")["format_id"]
    assert run.get("review") == REVIEW
    pkg = run.get("packaging")
    assert pkg["publish_title"] == "Why the AI chip boom changes 2027 forever"
    assert pkg["thumb_hook"] == "CHIPS ARE EVERYTHING"
    assert run.slug == pkg["slug"] and run.slug.endswith("_20261126")
    pending = read_json(run.path("packaging_pending.json"))
    assert pending["topic"] == "AI chip boom"
    assert pending["slug"] == run.slug
    assert pending["approved"] is False and pending["rejected"] is False
    assert pending["chosen_index"] is None
    assert pending["format_id"] == run.get("pick_format")["format_id"]
    assert pending["winner"]["variant_id"] == "v1"


def test_no_pending_file_without_approval_setting(tmp_path, settings, monkeypatch):
    e = _make_env(tmp_path, settings, monkeypatch, _responses(), packaging_require_approval=False)
    _run_all(e.ctx)
    assert not e.ctx.run.path("packaging_pending.json").exists()


def test_empty_ranking_uses_placeholder_topic(tmp_path, settings, monkeypatch):
    e = _make_env(
        tmp_path, settings, monkeypatch,
        _responses(rank_topics={"topics": []}), channel_primary_niche=[],
    )
    monkeypatch.setattr(text, "fetch_trending_topics", lambda s: [])
    _run_all(e.ctx)
    sel = e.ctx.run.get("rank_topics")["selected"]
    assert sel["title"] == "Today's Trending Topic"
    assert sel["angle"] == "Daily Trending"  # Thursday, deterministic content_angle


def test_llm_failure_falls_back_to_raw_signals(tmp_path, settings, monkeypatch):
    e = _make_env(
        tmp_path, settings, monkeypatch,
        _responses(rank_topics=ProviderError("down")), channel_primary_niche=[],
    )
    _run_all(e.ctx)
    data = e.ctx.run.get("rank_topics")
    assert len(data["topics"]) == 3
    assert {t["title"] for t in data["topics"]} <= set(SIGNALS)
    assert all(t["audience"] == "general" for t in data["topics"])
    assert data["selected"]["angle"] == "Daily Trending"


def test_niche_hard_filter_runs_before_rerank(tmp_path, settings, monkeypatch):
    e = _make_env(
        tmp_path, settings, monkeypatch, _responses(),
        channel_primary_niche=["cricket"], niche_lock_enabled=True,
    )
    _run_all(e.ctx)
    titles = [t["title"] for t in e.ctx.run.get("rank_topics")["topics"]]
    assert titles == ["Cricket final"]
    assert "NICHE LOCK" in e.fake.requests[0].prompt  # rank prompt
    script_req = next(r for r in e.fake.requests if r.task == "write_script")
    assert "NICHE LOCK" in script_req.prompt


def test_niche_note_empty_when_lock_disabled(tmp_path, settings, monkeypatch):
    e = _make_env(
        tmp_path, settings, monkeypatch, _responses(),
        channel_primary_niche=["cricket"], niche_lock_enabled=False,
    )
    _run_all(e.ctx)
    assert all("NICHE LOCK" not in r.prompt for r in e.fake.requests)
    assert len(e.ctx.run.get("rank_topics")["topics"]) == 2  # no hard filter


def test_script_prompt_includes_memory_notes(env):
    write_json_atomic(
        env.ctx.tts_memory.path,
        {
            "runs": 2, "last_updated": "", "pronunciation_corrections": {},
            "hesitation_words": {}, "style_notes": ["TTS-NOTE-X"], "stats_history": [],
        },
    )
    _run_all(env.ctx)
    script_req = next(r for r in env.fake.requests if r.task == "write_script")
    assert "TTS-NOTE-X" in script_req.prompt
    # supporting signals: remaining ranked topics, then raw signals
    assert "Cricket final" in script_req.prompt


def test_pending_not_clobbered_when_already_approved(env):
    _run_all(env.ctx)
    path = env.ctx.run.path("packaging_pending.json")
    approved = read_json(path)
    approved.update(approved=True, chosen_index=1)
    write_json_atomic(path, approved)
    env.ctx.run._state["stages"].pop("packaging")  # re-run packaging stage only
    text.PackagingStage().run(env.ctx)
    assert read_json(path)["approved"] is True
    assert read_json(path)["chosen_index"] == 1


def test_pick_format_receives_ctx_today(env, monkeypatch):
    seen: dict = {}
    real = text.pick_format

    def spy(topic, angle="", today=None):
        seen["today"] = today
        return real(topic, angle=angle, today=today)

    monkeypatch.setattr(text, "pick_format", spy)
    _run_all(env.ctx)
    assert seen["today"] == TODAY
