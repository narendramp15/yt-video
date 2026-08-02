"""Phase B helper smoke tests — captions, topic re-rank, packaging scores."""

import os
import tempfile

from services.sync_service import group_caption_cues, words_to_srt
from services.topic_service import rerank_topics
from services.packaging_service import pick_best_packaging, score_packaging_variant


def test_group_caption_cues_bounds():
    words = [
        {"word": "This", "start": 0.0, "end": 0.2},
        {"word": "is", "start": 0.2, "end": 0.35},
        {"word": "a", "start": 0.35, "end": 0.4},
        {"word": "reasonably", "start": 0.4, "end": 0.8},
        {"word": "long", "start": 0.8, "end": 1.0},
        {"word": "caption", "start": 1.0, "end": 1.3},
        {"word": "line", "start": 1.3, "end": 1.5},
        {"word": "about", "start": 1.5, "end": 1.7},
        {"word": "markets", "start": 1.7, "end": 2.1},
        {"word": "today", "start": 2.1, "end": 2.4},
    ]
    cues = group_caption_cues(words, max_chars=20, max_duration=1.2)
    assert cues
    assert all("text" in c and "start" in c and "end" in c for c in cues)
    assert all(len(c["text"]) <= 28 for c in cues)
    assert cues[0]["start"] == 0.0
    assert cues[-1]["end"] >= cues[0]["end"]


def test_words_to_srt_timing():
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.8},
    ]
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "t.srt")
        words_to_srt(words, path)
        text = open(path, encoding="utf-8").read()
        assert "1\n" in text
        assert "00:00:00,000 -->" in text
        assert "Hello" in text


def test_rerank_topics_novelty_penalty():
    ranked = [
        {"title": "ChatGPT agent launch India", "angle": "AI tools", "score": 8.0},
        {"title": "IPL final preview tonight", "angle": "cricket", "score": 7.5},
    ]
    recent = ["Yesterday's ChatGPT agent launch India deep dive"]
    signals = [
        "IPL final preview tonight trending",
        "Fans talk IPL final preview",
        "Stadium ready for IPL final",
        "Unrelated tech gadget rumor",
    ]
    out = rerank_topics(ranked, raw_signals=signals, recent_topics=recent, top_n=2)
    assert out[0]["title"].startswith("IPL")
    # Novel cricket topic should outrank the recently-covered ChatGPT topic
    chatgpt = next(x for x in out if "ChatGPT" in x["title"])
    assert chatgpt["rerank_delta"] < 0


def test_packaging_score_ordering():
    a = {"title": "UPI Hits 15 Billion Transactions", "hook": "UPI RECORD HIT"}
    b = {"title": "Stuff", "hook": ""}
    assert score_packaging_variant(a) > score_packaging_variant(b)
    assert pick_best_packaging([b, a])["title"].startswith("UPI")


if __name__ == "__main__":
    test_group_caption_cues_bounds()
    test_words_to_srt_timing()
    test_rerank_topics_novelty_penalty()
    test_packaging_score_ordering()
    print("phase_b helper tests passed")
