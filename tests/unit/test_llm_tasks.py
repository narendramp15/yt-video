"""Tests for yt_video.llm_tasks and prompt templates."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_models import make_script
from yt_video import llm_tasks
from yt_video.models import ScriptOutput
from yt_video.prompts import render_prompt
from yt_video.providers.llm.base import LLMError, ProviderError
from yt_video.providers.llm.fake import FakeLLM
from yt_video.providers.llm.router import LLMRouter

PROMPT_DIR = Path(llm_tasks.__file__).parent / "prompts"


def _router(responses: dict) -> tuple[LLMRouter, FakeLLM]:
    fake = FakeLLM(responses)
    return LLMRouter([fake], None, "r"), fake


def test_render_prompt_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        render_prompt("review")


def test_write_script_passes_notes_and_tier() -> None:
    router, fake = _router({"write_script": make_script()})
    out = llm_tasks.write_script(
        router, "Topic A", "Daily Trending", ["sig1", "sig2"],
        "FORMAT-X", "TTS-NOTE-X", "ANALYTICS-X",
    )
    assert isinstance(out, ScriptOutput)
    req = fake.requests[0]
    assert req.tier == "script"
    assert req.schema is ScriptOutput
    for needle in ("TTS-NOTE-X", "FORMAT-X", "ANALYTICS-X", "Topic A", "sig1", "Daily Trending"):
        assert needle in req.prompt


def test_rank_topics_returns_list_and_empty() -> None:
    topics = [{"title": "T", "angle": "a", "audience": "u", "score": 9.0}]
    router, fake = _router({"rank_topics": {"topics": topics}})
    out = llm_tasks.rank_topics(router, ["x", "y"], ["old"], "NICHE-X", "AN-X", top_n=2)
    assert [t.title for t in out] == ["T"]
    assert fake.requests[0].tier == "light"
    assert "NICHE-X" in fake.requests[0].prompt and "1. x" in fake.requests[0].prompt
    router, _ = _router({"rank_topics": {"topics": []}})
    assert llm_tasks.rank_topics(router, ["x"], [], "", "") == []


def test_rank_topics_requests_v1_pool_size() -> None:
    router, fake = _router({"rank_topics": {"topics": []}})
    llm_tasks.rank_topics(router, ["x"], [], "", "", top_n=3)
    assert "top 9 topics" in fake.requests[0].prompt
    router, fake = _router({"rank_topics": {"topics": []}})
    llm_tasks.rank_topics(router, ["x"], [], "", "", top_n=6)
    assert "top 12 topics" in fake.requests[0].prompt


def test_write_script_injects_niche_note() -> None:
    router, fake = _router({"write_script": make_script()})
    llm_tasks.write_script(router, "T", "", [], "", "", "", niche_note="NICHE-LOCK-X")
    assert "NICHE-LOCK-X" in fake.requests[0].prompt


def test_packaging_returns_variants() -> None:
    v = [{"variant_id": "v1", "title": "T", "hook": "BIG NEWS"}]
    router, _ = _router({"packaging_variants": {"variants": v}})
    out = llm_tasks.packaging_variants(router, "topic", "base", "angle", 1)
    assert out[0].hook == "BIG NEWS"


def test_packaging_falls_back_on_llm_error() -> None:
    router, _ = _router({"packaging_variants": ProviderError("api")})
    out = llm_tasks.packaging_variants(router, "topic", "base title", "", 3)
    assert len(out) == 3


def test_review_returns_model() -> None:
    router, fake = _router({"review": {"score": 85, "safety": "Yes", "notes": "ok"}})
    r = llm_tasks.review(router, "SCRIPT-BODY")
    assert r.score == 85 and r.safety == "Yes"
    assert "SCRIPT-BODY" in fake.requests[0].prompt


def test_refine_fills_missing_from_initial() -> None:
    router, fake = _router({"refine_scene_prompts": {"scene_prompts": [{"label": "a", "prompt": "new a"}]}})
    out = llm_tasks.refine_scene_prompts(
        router, "t", "ang", ["a", "b"], {"a": "narr a"}, {"a": "old a", "b": "old b"}
    )
    assert out == {"a": "new a", "b": "old b"}
    assert fake.requests[0].tier == "script"
    assert "narr a" in fake.requests[0].prompt


def test_refine_returns_initial_on_error() -> None:
    router, _ = _router({"refine_scene_prompts": ProviderError("api")})
    initial = {"a": "old a"}
    assert llm_tasks.refine_scene_prompts(router, "t", None, ["a"], {}, initial) == initial


def test_refine_no_labels_skips_call() -> None:
    router, fake = _router({})
    assert llm_tasks.refine_scene_prompts(router, "t", None, [], {}, {}) == {}
    assert fake.requests == []


def test_llm_error_type_is_raised_by_router() -> None:
    router, _ = _router({"review": ProviderError("api")})
    with pytest.raises(LLMError):
        llm_tasks.review(router, "s")


def test_templates_have_two_sections_and_no_legacy_markers() -> None:
    for path in PROMPT_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# system\n"), path.name
        assert text.count("\n# prompt\n") == 1, path.name
        assert "[SEO_TITLE]" not in text and "/100" not in text, path.name
        assert "Score:" not in text, path.name
        assert "Return only JSON matching the provided schema." in text, path.name
