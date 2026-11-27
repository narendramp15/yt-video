"""Slug and scene-marker helpers."""

from datetime import date

from yt_video.domain.text import (
    make_slug,
    scene_labels,
    scene_texts,
    strip_scene_markers,
    strip_section_markers,
)

SCRIPT = "[SCENE:intro]\nHello there.\n[SCENE:s1]\nSecond part.\nMore."


def test_make_slug_strips_punctuation_and_adds_date():
    assert make_slug("Why Nifty's Crash? Explained!", date(2026, 11, 26)) == "why_niftys_crash_explained_20261126"


def test_make_slug_caps_length():
    slug = make_slug("word " * 40, date(2026, 1, 2))
    assert slug.endswith("_20260102")
    assert len(slug) == 60 + len("_20260102")


def test_scene_labels_and_texts():
    assert scene_labels(SCRIPT) == ["intro", "s1"]
    assert scene_texts(SCRIPT) == {"intro": "Hello there.", "s1": "Second part.\nMore."}


def test_strip_scene_markers_removes_non_spoken_lines():
    script = '[SCENE:intro]\n(pause)\nNarrator: "Hello world."\nPlain line.'
    assert strip_scene_markers(script) == "Hello world.\nPlain line."
    assert strip_section_markers(script) == strip_scene_markers(script)


def test_inline_scene_marker_is_stripped_from_narration():
    assert strip_scene_markers("[SCENE:s1] Hello there") == "Hello there"
    assert strip_scene_markers("Before [SCENE:s2] after.\nNext") == "Before after.\nNext"


def test_inline_marker_still_parsed_by_scene_alignment():
    script = "[SCENE:s1] Hello there\n[SCENE:s2] Bye"
    assert scene_labels(script) == ["s1", "s2"]
    assert scene_texts(script) == {"s1": "Hello there", "s2": "Bye"}
