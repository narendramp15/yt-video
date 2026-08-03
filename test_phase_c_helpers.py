"""Phase C helpers — niche hard filter + packaging gate JSON round-trip."""

import os
import tempfile

from services.topic_service import apply_niche_hard_filter, _tokenize
from services.packaging_service import (
    approve_packaging,
    reject_packaging,
    write_packaging_pending,
    load_packaging_pending,
    packaging_gate_status,
)
from services.yt_analytics_memory_service import (
    detect_weak_retention_bands,
    summarize_retention_curve,
)
from services.youtube_service import build_up_next_blurb
import config as config_mod


def test_niche_hard_filter_drops_off_niche():
    ranked = [
        {"title": "AI startup funding round", "angle": "tech", "score": 8.0},
        {"title": "Celebrity wedding photos", "angle": "gossip", "score": 9.5},
        {"title": "OpenAI model launch", "angle": "ai tools", "score": 7.0},
    ]
    old_niche = config_mod.config.CHANNEL_PRIMARY_NICHE
    old_lock = config_mod.config.NICHE_LOCK_ENABLED
    try:
        config_mod.config.CHANNEL_PRIMARY_NICHE = ["tech", "ai", "startup"]
        config_mod.config.NICHE_LOCK_ENABLED = True
        kept = apply_niche_hard_filter(ranked)
    finally:
        config_mod.config.CHANNEL_PRIMARY_NICHE = old_niche
        config_mod.config.NICHE_LOCK_ENABLED = old_lock

    titles = [k["title"] for k in kept]
    assert "Celebrity wedding photos" not in titles
    assert any("AI" in t or "OpenAI" in t for t in titles)


def test_niche_hard_filter_fallback_when_all_dropped():
    ranked = [
        {"title": "Celebrity wedding photos", "angle": "gossip", "score": 9.5},
        {"title": "Football transfer rumor", "angle": "sports", "score": 8.0},
    ]
    old_niche = config_mod.config.CHANNEL_PRIMARY_NICHE
    old_lock = config_mod.config.NICHE_LOCK_ENABLED
    try:
        config_mod.config.CHANNEL_PRIMARY_NICHE = ["tech", "ai", "startup"]
        config_mod.config.NICHE_LOCK_ENABLED = True
        kept = apply_niche_hard_filter(ranked)
    finally:
        config_mod.config.CHANNEL_PRIMARY_NICHE = old_niche
        config_mod.config.NICHE_LOCK_ENABLED = old_lock

    assert len(kept) == 1
    assert kept[0]["niche_forced"] is True
    assert "tech" in kept[0]["angle"].lower() or "ai" in kept[0]["angle"].lower()


def test_packaging_approve_reject_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "packaging_pending.json")
        old = config_mod.config.PACKAGING_PENDING_PATH
        old_req = config_mod.config.PACKAGING_REQUIRE_APPROVAL
        try:
            config_mod.config.PACKAGING_PENDING_PATH = path
            variants = [
                {"title": "Title A", "hook": "HOOK A HERE", "variant_id": "v1", "score": 5.0},
                {
                    "title": "Title B With Number 12",
                    "hook": "NUMBER HIT NOW",
                    "variant_id": "v2",
                    "score": 8.0,
                },
            ]
            write_packaging_pending(
                topic="AI chips",
                angle="tech",
                variants=variants,
                winner=variants[0],
                slug="ai-chips",
                format_id="numbers",
            )
            pending = load_packaging_pending()
            assert pending is not None
            assert pending["approved"] is False

            approved = approve_packaging(variant_index=1)
            assert approved["approved"] is True
            assert approved["chosen"]["variant_id"] == "v2"
            assert approved["force_private"] is False

            write_packaging_pending(
                topic="AI chips",
                angle="tech",
                variants=variants,
                winner=variants[0],
            )
            rejected = reject_packaging()
            assert rejected["approved"] is True
            assert rejected["force_private"] is True

            config_mod.config.PACKAGING_REQUIRE_APPROVAL = True
            status = packaging_gate_status()
            assert status["requires_gate"] is True
            assert status["force_private"] is True
        finally:
            config_mod.config.PACKAGING_PENDING_PATH = old
            config_mod.config.PACKAGING_REQUIRE_APPROVAL = old_req


def test_detect_weak_retention_bands():
    curve = []
    for i in range(0, 101, 5):
        ratio = i / 100.0
        if ratio < 0.40:
            ret = 90.0 - ratio * 10
        elif ratio < 0.55:
            ret = 80.0 - (ratio - 0.40) * 120
        else:
            ret = 55.0 - (ratio - 0.55) * 20
        curve.append({"elapsed_ratio": ratio, "retention": ret})

    bands = detect_weak_retention_bands(curve, drop_pts=15.0, window=0.10)
    assert bands, "expected at least one weak band"
    notes = " ".join(b.get("note") or "" for b in bands).lower()
    assert "drop" in notes
    summary = summarize_retention_curve(curve, bands)
    assert summary["available"] is True
    assert summary["weak_band_count"] >= 1


def test_build_up_next_blurb():
    blurb = build_up_next_blurb(["abc", "def"])
    assert "Watch next:" in blurb
    assert "abc" in blurb
    assert "def" in blurb
    assert build_up_next_blurb([]) == ""


def test_niche_hard_filter_multiword_keywords():
    ranked = [
        {"title": "Machine learning model launch", "angle": "deep dive", "score": 8.0},
        {"title": "Celebrity wedding photos", "angle": "gossip", "score": 9.5},
    ]
    old_niche = config_mod.config.CHANNEL_PRIMARY_NICHE
    old_lock = config_mod.config.NICHE_LOCK_ENABLED
    try:
        config_mod.config.CHANNEL_PRIMARY_NICHE = ["machine learning", "ai"]
        config_mod.config.NICHE_LOCK_ENABLED = True
        kept = apply_niche_hard_filter(ranked)
    finally:
        config_mod.config.CHANNEL_PRIMARY_NICHE = old_niche
        config_mod.config.NICHE_LOCK_ENABLED = old_lock

    titles = [k["title"] for k in kept]
    assert "Machine learning model launch" in titles
    assert "Celebrity wedding photos" not in titles


def test_tokenize_keeps_short_niche_tokens():
    tokens = _tokenize("AI startup in India")
    assert "ai" in tokens
    assert "startup" in tokens


if __name__ == "__main__":
    test_niche_hard_filter_drops_off_niche()
    test_niche_hard_filter_fallback_when_all_dropped()
    test_packaging_approve_reject_roundtrip()
    test_detect_weak_retention_bands()
    test_build_up_next_blurb()
    test_niche_hard_filter_multiword_keywords()
    test_tokenize_keeps_short_niche_tokens()
    print("phase_c helper tests passed")
