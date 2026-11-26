"""Tests for yt_video.models: schemas and script quality bars."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_video.models import (
    ContentAngle,
    PackagingVariants,
    RankedTopics,
    Review,
    ScenePromptList,
    ScriptOutput,
    ScoredVariant,
    llm_schema,
)


def make_script(
    words: int = 1500,
    scenes: int = 30,
    shorts: int = 150,
    title: str = "T" * 60,
    desc: str = "d" * 450,
    tags: int = 10,
) -> dict:
    """Build a valid-by-default ScriptOutput payload.

    ``words`` counts words excluding scene markers; ``scenes`` is the
    number of ``[SCENE:label]`` markers.
    """
    markers = [f"[SCENE:s{i}]" for i in range(scenes)]
    plain = ["word"] * words
    # Interleave markers evenly through the words.
    tokens: list[str] = []
    step = max(1, words // max(1, scenes))
    mi = 0
    for i, w in enumerate(plain):
        if mi < len(markers) and i % step == 0:
            tokens.append(markers[mi])
            mi += 1
        tokens.append(w)
    tokens.extend(markers[mi:])
    return {
        "long_script": " ".join(tokens),
        "shorts_script": " ".join(["short"] * shorts),
        "seo_title": title,
        "seo_description": desc,
        "seo_tags": [f"tag{i}" for i in range(tags)],
        "scene_prompts": [{"label": f"s{i}", "prompt": f"prompt {i}"} for i in range(scenes)],
        "thumbnail_prompt": "a thumbnail",
    }


def test_valid_script_passes() -> None:
    ScriptOutput.model_validate(make_script())


@pytest.mark.parametrize(
    "kw",
    [
        dict(words=1199),
        dict(words=1901),
        dict(scenes=23),
        dict(scenes=37),
        dict(shorts=109),
        dict(shorts=181),
        dict(title="T" * 71),
        dict(desc="d" * 399),
        dict(desc="d" * 481),
        dict(tags=9),
        dict(tags=11),
    ],
)
def test_quality_bars_reject(kw: dict) -> None:
    with pytest.raises(ValidationError):
        ScriptOutput.model_validate(make_script(**kw))


@pytest.mark.parametrize(
    "kw",
    [dict(words=1200), dict(words=1900), dict(scenes=24), dict(scenes=36),
     dict(shorts=110), dict(shorts=180), dict(title="T" * 70),
     dict(desc="d" * 400), dict(desc="d" * 480)],
)
def test_quality_bar_boundaries_accepted(kw: dict) -> None:
    ScriptOutput.model_validate(make_script(**kw))


def test_scene_marker_words_not_counted() -> None:
    # 1200 real words + 30 markers: counting markers would give 1230 (still
    # valid), so check the lower edge: 1199 words + 30 markers must fail
    # even though raw token count is 1229.
    ScriptOutput.model_validate(make_script(words=1200, scenes=30))
    with pytest.raises(ValidationError):
        ScriptOutput.model_validate(make_script(words=1199, scenes=30))


def test_scene_prompt_map() -> None:
    script = ScriptOutput.model_validate(make_script())
    assert script.scene_prompt_map["s0"] == "prompt 0"
    assert len(script.scene_prompt_map) == 30
    assert "scene_prompt_map" not in ScriptOutput.model_fields


def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for v in node.values():
            yield from _walk_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_objects(v)


@pytest.mark.parametrize(
    "model",
    [ScriptOutput, RankedTopics, PackagingVariants, Review, ScenePromptList, ContentAngle],
)
def test_llm_schema_strict(model) -> None:
    sch = llm_schema(model)
    assert sch["additionalProperties"] is False
    assert set(sch["required"]) == set(model.model_fields)
    objs = list(_walk_objects(sch))
    assert objs
    for obj in objs:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    assert "default" not in str(sch)


def test_llm_schema_has_defs_for_nested() -> None:
    sch = llm_schema(ScriptOutput)
    assert "ScenePrompt" in sch["$defs"]
    assert sch["$defs"]["ScenePrompt"]["additionalProperties"] is False


def test_llm_schema_does_not_mutate_model_schema() -> None:
    llm_schema(ScriptOutput)
    assert "additionalProperties" not in ScriptOutput.model_json_schema()


def test_review_score_bounds() -> None:
    with pytest.raises(ValidationError):
        Review(score=101, safety="Yes", notes="")
    with pytest.raises(ValidationError):
        Review(score=-1, safety="Yes", notes="")
    Review(score=0, safety="No", notes="")
    Review(score=100, safety="Yes", notes="")


def test_review_safety_literal() -> None:
    with pytest.raises(ValidationError):
        Review(score=50, safety="Maybe", notes="")


def test_scored_variant_extends_variant() -> None:
    v = ScoredVariant(variant_id="a", title="t", hook="h", score=0.5)
    assert v.score == 0.5
