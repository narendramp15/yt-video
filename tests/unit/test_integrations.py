"""Integrations: no network, no real credentials."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yt_video.integrations import trends, x, youtube
from yt_video.memory.analytics_memory import ANALYTICS_SCOPE


def _stub_sources(monkeypatch, **overrides):
    defaults = {
        "fetch_x_trends": lambda *a, **k: [],
        "fetch_google_trends_topics": lambda *a, **k: [],
        "fetch_rss_topics": lambda *a, **k: [],
        "fetch_yfinance_news_topics": lambda *a, **k: [],
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(trends, name, fn)


def test_trends_source_failure_isolated(monkeypatch, settings):
    def boom(*a, **k):
        raise RuntimeError("trends down")

    _stub_sources(
        monkeypatch,
        fetch_google_trends_topics=boom,
        fetch_rss_topics=lambda *a, **k: ["A", "B", "A"],
    )
    assert trends.fetch_trending_topics(settings) == ["A", "B"]


def test_trends_dedupe_is_case_insensitive_and_ordered(monkeypatch, settings):
    _stub_sources(
        monkeypatch,
        fetch_x_trends=lambda *a, **k: ["Nifty", "ipl"],
        fetch_rss_topics=lambda *a, **k: [" nifty ", "IPL", "Budget"],
    )
    assert trends.fetch_trending_topics(settings) == ["Nifty", "ipl", "Budget"]


def test_x_post_noop_without_creds(tmp_path, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(x, "OAuth1Session", lambda **k: calls.append(k))
    log = tmp_path / "state" / "x_post_log.json"
    assert x.post_video_update(settings, log, title="T", video_id="vid1") is None
    assert calls == []
    assert not log.exists()


def _creds_settings(settings):
    return settings.model_copy(update={
        "x_consumer_key": "a", "x_consumer_secret": "b",
        "x_access_token": "c", "x_access_token_secret": "d",
    })


def test_x_post_success_logs_and_dedupes(tmp_path, settings, monkeypatch):
    posted = []

    class FakeSession:
        def __init__(self, **kw):
            pass

        def post(self, url, json=None, timeout=None):
            posted.append((url, json))
            return SimpleNamespace(
                status_code=200, text="", json=lambda: {"data": {"id": "t1"}}
            )

    monkeypatch.setattr(x, "OAuth1Session", FakeSession)
    s = _creds_settings(settings)
    log = tmp_path / "state" / "x_post_log.json"
    assert x.post_video_update(s, log, title="Hello", video_id="v1", angle="why") == "t1"
    assert posted[0][0] == "https://api.x.com/2/tweets"
    assert posted[0][1]["text"] == "Hello\n\nwhy\n\nhttps://www.youtube.com/watch?v=v1"
    assert json.loads(log.read_text(encoding="utf-8"))["posts"][0]["video_id"] == "v1"
    assert x.post_video_update(s, log, title="Hello", video_id="v1") is None
    assert len(posted) == 1


def test_youtube_client_lazy(tmp_path):
    client = youtube.YouTubeClient(
        tmp_path / "missing_secret.json", tmp_path / "tok.json", tmp_path / "pl.json"
    )
    assert client is not None
    assert not (tmp_path / "tok.json").exists()


def _client(tmp_path):
    return youtube.YouTubeClient(
        tmp_path / "secret.json", tmp_path / "tok.json", tmp_path / "pl.json"
    )


def test_scopes_include_analytics():
    assert ANALYTICS_SCOPE in youtube.SCOPES
    assert "https://www.googleapis.com/auth/youtube.upload" in youtube.SCOPES


def test_credentials_reuses_token_with_all_scopes(tmp_path, monkeypatch):
    creds = SimpleNamespace(valid=True, expired=False, refresh_token=None,
                            scopes=list(youtube.SCOPES))
    (tmp_path / "tok.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        youtube.Credentials, "from_authorized_user_file", lambda p, s=None: creds
    )
    flow = []
    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file",
                        lambda *a, **k: flow.append(1))
    assert _client(tmp_path).credentials() is creds
    assert flow == []


def test_credentials_missing_scope_reruns_flow(tmp_path, monkeypatch):
    old = SimpleNamespace(valid=True, expired=False, refresh_token="r",
                          scopes=youtube.SCOPES[:2])
    (tmp_path / "tok.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        youtube.Credentials, "from_authorized_user_file", lambda p, s=None: old
    )
    new = SimpleNamespace(to_json=lambda: json.dumps({"token": "new"}))

    class FakeFlow:
        def run_local_server(self, port=0):
            return new

    seen = {}

    def fake_from_secrets(path, scopes):
        seen["scopes"] = scopes
        return FakeFlow()

    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file",
                        fake_from_secrets)
    assert _client(tmp_path).credentials() is new
    assert seen["scopes"] == youtube.SCOPES
    assert json.loads((tmp_path / "tok.json").read_text(encoding="utf-8")) == {"token": "new"}


def test_ensure_playlist_uses_cache_without_api(tmp_path):
    (tmp_path / "pl.json").write_text(json.dumps({"Show": "PL1"}), encoding="utf-8")
    assert _client(tmp_path).ensure_playlist("Show") == "PL1"


def test_pinned_comment_failure_is_nonfatal(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(c, "_api", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    c.post_pinned_comment("vid", "hello")  # must not raise
    assert c.add_video_to_playlist("pl", "vid") is False
