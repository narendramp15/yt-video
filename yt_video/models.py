"""Pydantic models shared by the pipeline stages and the LLM clients.

Models used as LLM output schemas have only required fields (no defaults,
no dicts) so one JSON Schema serves both ``claude --json-schema`` and
OpenAI strict structured outputs. Constraints are enforced by validators,
not ``Field`` keywords, to keep the emitted schema within the strict subset.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

SCENE_MARKER_RE = re.compile(r"\[SCENE:[^\]]+\]")

# Acceptance bounds, deliberately wider than the targets the prompt asks for
# (prompts/write_script.md: 1200-1900 words, 24-36 markers, 110-180 Shorts
# words, title <= 70, description 400-480, exactly 10 tags). LLMs miss word
# and character counts by 5-15%; a near miss must not fail the run. These only
# reject output that is genuinely broken.
LONG_WORDS = (1000, 2200)
SCENE_MARKERS = (20, 40)
SHORTS_WORDS = (90, 220)
SEO_TITLE_MAX = 100          # YouTube's hard title limit
SEO_DESC_LEN = (250, 700)
SEO_TAG_RANGE = (8, 15)      # accepted count; extras beyond SEO_TAG_COUNT are trimmed
SEO_TAG_COUNT = 10
REVIEW_SCORE = (0, 100)


def _word_count(text: str) -> int:
    """Whitespace-split word count with ``[SCENE:...]`` markers removed."""
    return len(SCENE_MARKER_RE.sub(" ", text).split())


class Topic(BaseModel):
    title: str
    angle: str
    audience: str
    score: float


class RankedTopics(BaseModel):
    topics: list[Topic]


class ScenePrompt(BaseModel):
    label: str
    prompt: str


class ScriptOutput(BaseModel):
    long_script: str
    shorts_script: str
    seo_title: str
    seo_description: str
    seo_tags: list[str]
    scene_prompts: list[ScenePrompt]
    thumbnail_prompt: str

    @property
    def scene_prompt_map(self) -> dict[str, str]:
        return {sp.label: sp.prompt for sp in self.scene_prompts}

    @field_validator("long_script")
    @classmethod
    def _check_long_script(cls, v: str) -> str:
        words = _word_count(v)
        lo, hi = LONG_WORDS
        if not lo <= words <= hi:
            raise ValueError(f"long_script must have {lo}-{hi} words (got {words})")
        markers = len(SCENE_MARKER_RE.findall(v))
        lo, hi = SCENE_MARKERS
        if not lo <= markers <= hi:
            raise ValueError(f"long_script must have {lo}-{hi} [SCENE:] markers (got {markers})")
        return v

    @field_validator("shorts_script")
    @classmethod
    def _check_shorts(cls, v: str) -> str:
        words = _word_count(v)
        lo, hi = SHORTS_WORDS
        if not lo <= words <= hi:
            raise ValueError(f"shorts_script must have {lo}-{hi} words (got {words})")
        return v

    @field_validator("seo_title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        if len(v) > SEO_TITLE_MAX:
            raise ValueError(f"seo_title must be <= {SEO_TITLE_MAX} chars (got {len(v)})")
        return v

    @field_validator("seo_description")
    @classmethod
    def _check_description(cls, v: str) -> str:
        lo, hi = SEO_DESC_LEN
        if not lo <= len(v) <= hi:
            raise ValueError(f"seo_description must be {lo}-{hi} chars (got {len(v)})")
        return v

    @field_validator("seo_tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        lo, hi = SEO_TAG_RANGE
        if not lo <= len(v) <= hi:
            raise ValueError(f"seo_tags must contain {lo}-{hi} tags (got {len(v)})")
        return v[:SEO_TAG_COUNT]


class PackagingVariant(BaseModel):
    variant_id: str
    title: str
    hook: str


class PackagingVariants(BaseModel):
    variants: list[PackagingVariant]


class ScoredVariant(PackagingVariant):
    """Internal (not LLM-facing): a variant plus its computed score."""

    score: float


class Review(BaseModel):
    score: int
    safety: Literal["Yes", "No"]
    notes: str

    @field_validator("score")
    @classmethod
    def _check_score(cls, v: int) -> int:
        lo, hi = REVIEW_SCORE
        if not lo <= v <= hi:
            raise ValueError(f"score must be between {lo} and {hi} (got {v})")
        return v


class ScenePromptList(BaseModel):
    scene_prompts: list[ScenePrompt]


class ContentAngle(BaseModel):
    angle: str


def _strictify(node: Any) -> None:
    """Recursively add ``additionalProperties: false`` and full ``required``."""
    if isinstance(node, dict):
        props = node.get("properties")
        if node.get("type") == "object" or isinstance(props, dict):
            node["additionalProperties"] = False
            node["required"] = list((props or {}).keys())
        node.pop("default", None)
        for value in node.values():
            _strictify(value)
    elif isinstance(node, list):
        for item in node:
            _strictify(item)


def llm_schema(model: type[BaseModel]) -> dict:
    """JSON Schema for ``model`` valid for OpenAI strict mode and Claude CLI.

    Every object (including those in ``$defs``) gets
    ``additionalProperties: false`` and lists all its properties as required.
    """
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema)
    return schema
