"""Smoke tests for packaging helpers (Phase A + B)."""

from services.packaging_service import (
    build_publish_description,
    decide_privacy,
    derive_thumbnail_hook,
    format_chapter_timestamps,
    humanize_scene_label,
    pick_best_packaging,
    resolve_category_id,
    score_packaging_variant,
)


def test_derive_thumbnail_hook():
    # Prefer punchy clause before a separator
    assert derive_thumbnail_hook("Reliance AI Bet: What Changes Now") == "RELIANCE AI BET"
    assert derive_thumbnail_hook("Nifty Crash Explained Today") == "NIFTY CRASH EXPLAINED TODAY"
    assert derive_thumbnail_hook("") == "WATCH THIS"


def test_humanize_scene_label():
    assert humanize_scene_label("intro") == "Introduction"
    assert humanize_scene_label("s3") == "Part 3"
    assert "Reliance" in humanize_scene_label(
        "s1",
        "Reliance just committed a hundred billion dollars to AI infrastructure.",
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


def test_packaging_heuristic_picks_stronger_title():
    strong = {
        "title": "Reliance Just Bet $10B on AI Chips",
        "hook": "RELIANCE AI BET",
        "variant_id": "v1",
    }
    weak = {
        "title": "You Won't Believe What Happened Today In Tech News Wow",
        "hook": "WOW",
        "variant_id": "v2",
    }
    assert score_packaging_variant(strong) > score_packaging_variant(weak)
    best = pick_best_packaging([weak, strong])
    assert best["variant_id"] == "v1"


if __name__ == "__main__":
    test_derive_thumbnail_hook()
    test_humanize_scene_label()
    test_chapters_require_three_and_start_at_zero()
    test_build_description_appends_chapters()
    test_category_and_privacy()
    test_packaging_heuristic_picks_stronger_title()
    print("packaging_service tests passed")
