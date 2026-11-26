"""Topic re-rank, niche hard filter, tokenizer (ported from test_phase_b/c_helpers.py)."""

from datetime import date

import pytest

from yt_video.domain.topics import apply_niche_hard_filter, rerank_topics, tokenize
from yt_video.models import Topic


def _t(title: str, angle: str, score: float) -> Topic:
    return Topic(title=title, angle=angle, audience="general", score=score)


def test_rerank_topics_novelty_penalty():
    ranked = [
        _t("ChatGPT agent launch India", "AI tools", 8.0),
        _t("IPL final preview tonight", "cricket", 7.5),
    ]
    recent = ["Yesterday's ChatGPT agent launch India deep dive"]
    signals = [
        "IPL final preview tonight trending",
        "Fans talk IPL final preview",
        "Stadium ready for IPL final",
        "Unrelated tech gadget rumor",
    ]
    out = rerank_topics(ranked, recent, [], [], raw_signals=signals, top_n=2)
    assert out[0].title.startswith("IPL")
    # Novel cricket topic should outrank the recently-covered ChatGPT topic
    chatgpt = next(x for x in out if "ChatGPT" in x.title)
    # v1 asserted rerank_delta < 0; Topic has no delta field, so the penalty shows as score < original 8.0
    assert chatgpt.score - 8.0 < 0


def test_niche_hard_filter_drops_off_niche():
    ranked = [
        _t("AI startup funding round", "tech", 8.0),
        _t("Celebrity wedding photos", "gossip", 9.5),
        _t("OpenAI model launch", "ai tools", 7.0),
    ]
    kept = apply_niche_hard_filter(ranked, ["tech", "ai", "startup"])

    titles = [k.title for k in kept]
    assert "Celebrity wedding photos" not in titles
    assert any("AI" in t or "OpenAI" in t for t in titles)


def test_niche_hard_filter_fallback_when_all_dropped():
    ranked = [
        _t("Celebrity wedding photos", "gossip", 9.5),
        _t("Football transfer rumor", "sports", 8.0),
    ]
    kept = apply_niche_hard_filter(ranked, ["tech", "ai", "startup"])

    assert len(kept) == 1
    # v1 also asserted kept[0]["niche_forced"] is True; Topic has no such flag, so check the forced angle
    assert kept[0].angle.startswith("Frame through tech, ai, startup:")
    assert kept[0].title == "Celebrity wedding photos"
    assert "tech" in kept[0].angle.lower() or "ai" in kept[0].angle.lower()


def test_niche_hard_filter_multiword_keywords():
    ranked = [
        _t("Machine learning model launch", "deep dive", 8.0),
        _t("Celebrity wedding photos", "gossip", 9.5),
    ]
    kept = apply_niche_hard_filter(ranked, ["machine learning", "ai"])

    titles = [k.title for k in kept]
    assert "Machine learning model launch" in titles
    assert "Celebrity wedding photos" not in titles


def test_tokenize_keeps_short_niche_tokens():
    tokens = tokenize("AI startup in India")
    assert "ai" in tokens
    assert "startup" in tokens


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 11, 23), "Weekly Preview"),   # Monday
        (date(2026, 11, 27), "Weekend Deep-Dive"),  # Friday
        (date(2026, 11, 25), "Daily Trending"),   # Wednesday
        (date(2026, 11, 29), "Daily Trending"),   # Sunday
    ],
)
def test_content_angle_by_weekday(day: date, expected: str) -> None:
    from yt_video.domain.topics import content_angle

    assert content_angle("any", day) == expected
