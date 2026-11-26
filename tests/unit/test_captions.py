"""Caption helpers (ported from test_phase_b_helpers.py)."""

from yt_video.domain.captions import group_caption_cues, load_caption_cues, words_to_srt


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


def test_words_to_srt_timing(tmp_path):
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.8},
    ]
    path = tmp_path / "t.srt"
    assert words_to_srt(words, path) == path
    text = path.read_text(encoding="utf-8")
    assert "1\n" in text
    assert "00:00:00,000 -->" in text
    assert "Hello" in text


def test_load_caption_cues_handles_missing_and_bad_files(tmp_path):
    assert load_caption_cues(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_caption_cues(bad) == []
    good = tmp_path / "ok.json"
    good.write_text('[{"word": "Hi", "start": 0.0, "end": 0.3}]', encoding="utf-8")
    assert load_caption_cues(good) == [{"text": "Hi", "start": 0.0, "end": 0.3}]
