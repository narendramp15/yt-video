"""Publish helpers (ported from test_packaging_service.py and test_phase_c_helpers.py)."""

from yt_video.domain.publish import (
    build_publish_description,
    build_up_next_blurb,
    decide_privacy,
    format_chapter_timestamps,
    resolve_category_id,
    shorts_title,
)


def test_chapters_require_three_and_start_at_zero():
    timeline = {"intro": 1.5, "s1": 40.0, "s2": 90.0, "outro": 500.0}
    titles = {"intro": "Introduction", "s1": "The Bet", "s2": "Why Now", "outro": "Key Takeaway"}
    block = format_chapter_timestamps(timeline, titles)
    assert block.startswith("Chapters:")
    assert "0:00 Introduction" in block
    assert "1:30 Why Now" in block or "1:30" in block


def test_build_description_appends_chapters():
    desc = build_publish_description(
        "A short SEO blurb about today's trend.",
        timeline={"intro": 0, "s1": 30, "s2": 70},
        scene_titles={"intro": "Introduction", "s1": "Context", "s2": "Impact"},
    )
    assert "A short SEO blurb" in desc
    assert "0:00 Introduction" in desc


def test_category_and_privacy():
    assert resolve_category_id("ChatGPT launches new AI agent", "OpenAI news") == "28"
    assert resolve_category_id("IPL final preview", "Cricket") == "17"
    assert resolve_category_id("Random lifestyle vlog", "My day") == "22"
    # Substring "ai" must not match inside unrelated words like "daily"
    assert resolve_category_id("My daily routine vlog", "Said nothing special") == "22"
    assert decide_privacy(85, "Yes") == "public"
    assert decide_privacy(85, "No") == "private"
    assert decide_privacy(70, "Yes") == "private"


def test_build_up_next_blurb():
    blurb = build_up_next_blurb(["abc", "def"])
    assert "Watch next:" in blurb
    assert "abc" in blurb
    assert "def" in blurb
    assert build_up_next_blurb([]) == ""


def test_decide_privacy_missing_safety_is_private():
    assert decide_privacy(95, None) == "private"


def test_decide_privacy_threshold():
    assert decide_privacy(80, "Yes") == "public" and decide_privacy(79, "Yes") == "private"


def test_shorts_title_adds_tag_once():
    assert shorts_title("A #Shorts") == "A #Shorts"
    assert shorts_title("x" * 80).endswith(" #Shorts")
