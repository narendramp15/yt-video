import datetime
import json
import os

from yt_video.memory import analytics_memory as am
from yt_video.memory.analytics_memory import (
    AnalyticsMemory,
    detect_weak_retention_bands,
    summarize_retention_curve,
)
from yt_video.memory.tts_memory import TTSMemory


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


def _write_transcript(path):
    words = [
        {"word": "Today", "start": 0.0, "end": 0.4},
        {"word": "the", "start": 0.4, "end": 0.5},
        {"word": "API", "start": 1.0, "end": 1.4},
        {"word": "works", "start": 1.4, "end": 1.8},
    ]
    path.write_text(json.dumps(words), encoding="utf-8")


def test_tts_memory_roundtrip(tmp_path):
    transcript = tmp_path / "transcript.json"
    _write_transcript(transcript)
    mem_path = tmp_path / "state" / "tts.json"
    mem = TTSMemory(mem_path)
    assert mem.prompt_injection() == ""

    mem.analyze_and_update(transcript, "The API is here today. It works well enough.")

    assert mem.pronunciation_corrections() == {"API": "A P I"}
    assert "LEARNED TTS STYLE NOTES (from 1 previous run(s)" in mem.prompt_injection()
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [
        "transcript.json",
        "tts.json",
    ]
    assert TTSMemory(mem_path).pronunciation_corrections() == {"API": "A P I"}


def test_tts_memory_loads_v1_shaped_file(tmp_path):
    mem_path = tmp_path / "tts_style_memory.json"
    v1 = {
        "runs": 3,
        "last_updated": "2026-01-01",
        "pronunciation_corrections": {"GPT4": "GPT 4"},
        "hesitation_words": {"GPT4": 2},
        "style_notes": ["Keep it short."],
        "stats_history": [],
    }
    mem_path.write_text(json.dumps(v1), encoding="utf-8")
    mem = TTSMemory(mem_path)
    assert mem.pronunciation_corrections() == {"GPT4": "GPT 4"}
    assert "from 3 previous run(s)" in mem.prompt_injection()
    assert "Keep it short." in mem.prompt_injection()


def test_analytics_record_and_recent_ids(tmp_path):
    am_ = AnalyticsMemory(tmp_path / "m.json", tmp_path / "log.json", None)
    am_.record_video("a", "topic a", "angle", "Title A", "public")
    am_.record_video("b", "topic b", "angle", "Title B", "private")
    am_.record_video("c", "topic c", "angle", "Title C", "public")
    am_.record_video("c", "dup", "angle", "Dup", "public")  # duplicate ignored
    assert am_.recent_public_video_ids() == ["c", "a"]
    assert am_.recent_public_video_ids(exclude="c") == ["a"]
    assert am_.recent_video_topics(limit=3) == ["topic c", "Title C", "Title C"]


def test_analytics_loads_v1_shaped_file(tmp_path):
    path = tmp_path / "yt_analytics_memory.json"
    v1 = {
        "videos": [{
            "video_id": "v1", "topic": "AI", "angle": "x", "seo_title": "T",
            "title": "T", "thumb_hook": "", "packaging_variant_id": "",
            "format_id": "", "is_short": False, "privacy_status": "public",
            "upload_date": "2026-01-01", "analytics_ready": True,
            "analytics": {"views": 10, "averageViewPercentage": 50.0},
        }],
        "performance_summary": {
            "total_videos_analyzed": 1, "avg_retention": 50.0, "avg_views": 10.0,
            "top_topic_keywords": ["agents"], "weak_topic_keywords": [],
        },
        "topic_notes": ["Prefer agents."],
        "script_notes": ["Hook fast."],
        "last_updated": "2026-01-02",
    }
    path.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    mem = AnalyticsMemory(path, tmp_path / "log.json", None)
    assert mem.recent_public_video_ids() == ["v1"]
    assert mem.top_topic_keywords() == ["agents"]
    assert mem.topic_prompt_injection() == (
        "CHANNEL ANALYTICS INSIGHTS (from 1 published video(s) — use to score topics):\n"
        "  • Prefer agents.\n"
    )
    assert mem.script_prompt_injection() == (
        "CHANNEL PERFORMANCE NOTES (from 1 published video(s) — follow strictly):\n"
        "  • Hook fast.\n"
    )


def test_log_run_appends(tmp_path):
    log = tmp_path / "out" / "analytics_log.json"
    mem = AnalyticsMemory(tmp_path / "m.json", log, None)
    mem.log_run("vid", "topic", "title", "private")
    mem.log_run(None, "topic2", "title2", "public")
    rows = json.loads(log.read_text(encoding="utf-8"))
    assert [r["video_id"] for r in rows] == ["vid", None]
    assert rows[0]["privacy"] == "private"
    assert rows[0]["views_48h"] is None


def test_fetch_pending_without_credentials_returns_zero(tmp_path):
    mem = AnalyticsMemory(tmp_path / "m.json", tmp_path / "log.json", None)
    old = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    mem.record_video("a", "t", "a", "T", "public", upload_date=old)
    assert mem.fetch_pending() == 0


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeReports:
    def query(self, **kwargs):
        metrics = kwargs["metrics"]
        if metrics == "audienceRetention":
            return _FakeQuery({"rows": [[0.0, 100.0], [0.5, 60.0], [1.0, 40.0]]})
        if kwargs.get("dimensions") == "insightTrafficSourceType":
            return _FakeQuery({"rows": [["YT_SEARCH", 7.0]]})
        names = metrics.split(",")
        return _FakeQuery({
            "columnHeaders": [{"name": n} for n in names],
            "rows": [[120 if n == "views" else 55.0 if n == "averageViewPercentage"
                      else 3.0 for n in names]],
        })


class _FakeService:
    def reports(self):
        return _FakeReports()


def test_fetch_pending_with_fake_client(tmp_path, monkeypatch):
    built = []

    def fake_build(credentials):
        built.append(credentials)
        return _FakeService()

    monkeypatch.setattr(am, "_build_analytics_client", fake_build)
    mem = AnalyticsMemory(tmp_path / "m.json", tmp_path / "log.json", lambda: "CREDS")
    old = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    mem.record_video("old", "AI agents rising", "angle", "T", "public", upload_date=old)
    mem.record_video("new", "fresh", "angle", "T2", "public")

    assert mem.fetch_pending() == 1
    assert built == ["CREDS"]
    saved = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    by_id = {v["video_id"]: v for v in saved["videos"]}
    assert by_id["old"]["analytics_ready"] is True
    assert by_id["old"]["analytics"]["views"] == 120
    assert by_id["old"]["analytics"]["traffic_sources"] == {"YT_SEARCH": 7.0}
    assert by_id["new"]["analytics_ready"] is False
    assert saved["performance_summary"]["total_videos_analyzed"] == 1
    assert saved["script_notes"]


def test_no_writes_outside_given_paths(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    store = tmp_path / "store"

    transcript = store / "t.json"
    store.mkdir()
    _write_transcript(transcript)
    tts = TTSMemory(store / "tts.json")
    tts.analyze_and_update(transcript, "Some script words here today.")
    tts.prompt_injection()
    tts.pronunciation_corrections()

    mem = AnalyticsMemory(store / "m.json", store / "log.json", None)
    mem.record_video("a", "t", "a", "T", "public")
    mem.log_run("a", "t", "T", "public")
    mem.fetch_pending()
    mem.topic_prompt_injection()
    mem.script_prompt_injection()

    assert os.listdir(cwd) == []
    assert not list(store.glob("*.tmp"))
