"""Packaging helpers (ported from test_packaging_service.py and test_phase_b_helpers.py)."""

from yt_video.domain.packaging import (
    derive_thumbnail_hook,
    fallback_variants,
    humanize_scene_label,
    pick_best_packaging,
    score_packaging_variant,
)
from yt_video.models import PackagingVariant


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


def test_packaging_heuristic_picks_stronger_title():
    strong = PackagingVariant(
        title="Reliance Just Bet $10B on AI Chips",
        hook="RELIANCE AI BET",
        variant_id="v1",
    )
    weak = PackagingVariant(
        title="You Won't Believe What Happened Today In Tech News Wow",
        hook="WOW",
        variant_id="v2",
    )
    assert score_packaging_variant(strong) > score_packaging_variant(weak)
    best = pick_best_packaging([weak, strong])
    assert best.variant_id == "v1"


def test_packaging_score_ordering():
    a = PackagingVariant(title="UPI Hits 15 Billion Transactions", hook="UPI RECORD HIT", variant_id="a")
    b = PackagingVariant(title="Stuff", hook="", variant_id="b")
    assert score_packaging_variant(a) > score_packaging_variant(b)
    assert pick_best_packaging([b, a]).title.startswith("UPI")


def test_fallback_variants_deterministic():
    out = fallback_variants("Nifty Crash", "Nifty", 3)
    assert [v.variant_id for v in out] == ["v1", "v2", "v3"]
    assert out[1].title == "Nifty Crash — Explained"
    assert out[2].title == "What Changed: Nifty"
    assert len(fallback_variants("Nifty Crash", "Nifty", 1)) == 1
