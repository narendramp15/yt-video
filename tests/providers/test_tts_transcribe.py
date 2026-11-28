import subprocess
import sys

from yt_video.providers.tts import apply_corrections


def test_apply_corrections_whole_word():
    assert apply_corrections("API and RAPID", {"API": "A P I"}) == "A P I and RAPID"


def test_apply_corrections_case_sensitive_and_longest_first():
    out = apply_corrections("api API", {"API": "x"})
    assert out == "api x"
    assert apply_corrections("Nifty Bank", {"Nifty": "N", "Nifty Bank": "NB"}) == "NB"


def test_apply_corrections_empty():
    assert apply_corrections("hello", {}) == "hello"


def test_modules_import_without_heavy_deps():
    code = (
        "import sys, yt_video.providers.tts, yt_video.providers.transcribe;"
        "assert 'edge_tts' not in sys.modules;"
        "assert 'faster_whisper' not in sys.modules;"
        "assert 'openai' not in sys.modules"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


def _fake_openai(monkeypatch, **audio):
    import types

    class FakeOpenAI:
        def __init__(self, **kw):
            self.audio = types.SimpleNamespace(**audio)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))


def test_tts_fallback_records_ledger(tmp_path, monkeypatch):
    import sqlite3
    import types

    from yt_video.costs import CostLedger
    from yt_video.providers import tts
    from yt_video.settings import Settings

    def boom(*a, **k):
        raise RuntimeError("edge down")

    speech = types.SimpleNamespace(create=lambda **kw: types.SimpleNamespace(content=b"abc"))
    monkeypatch.setattr(tts, "_generate_edge", boom)
    monkeypatch.setattr(tts, "_normalize_audio", lambda p: None)
    _fake_openai(monkeypatch, speech=speech)
    ledger = CostLedger(tmp_path / "c.db")
    out = tts.generate_voice("Hello world.", tmp_path / "a" / "v.mp3", Settings(), {}, ledger, "r1")
    assert out.read_bytes() == b"abc"
    row = sqlite3.connect(tmp_path / "c.db").execute(
        "select stage, kind, provider, model, cost_usd, fallback_reason from calls"
    ).fetchone()
    assert row[:4] == ("voice", "tts", "openai", "tts-1")
    assert row[4] == 12 * 15 / 1_000_000
    assert row[5] == "edge_tts_failed"


def test_transcribe_fallback_records_ledger(tmp_path, monkeypatch):
    import sqlite3
    import types

    from yt_video.costs import CostLedger
    from yt_video.providers import transcribe
    from yt_video.settings import Settings

    def missing(_p):
        raise ImportError("no fw")

    word = types.SimpleNamespace(word="hi", start=0.0, end=30.0)
    tr = types.SimpleNamespace(
        create=lambda **kw: types.SimpleNamespace(words=[word], duration=60.0)
    )
    monkeypatch.setattr(transcribe, "_transcribe_local", missing)
    _fake_openai(monkeypatch, transcriptions=tr)
    voice = tmp_path / "v.mp3"
    voice.write_bytes(b"x")
    ledger = CostLedger(tmp_path / "c.db")
    words = transcribe.transcribe_words(voice, Settings(), ledger, "r1")
    assert words == [{"word": "hi", "start": 0.0, "end": 30.0}]
    row = sqlite3.connect(tmp_path / "c.db").execute(
        "select stage, kind, cost_usd, fallback_reason from calls"
    ).fetchone()
    assert row == ("transcribe", "transcribe", 0.006, "faster_whisper_unavailable")


import pytest  # noqa: E402


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_generate_voice_rejects_empty_text(tmp_path, text):
    from yt_video.providers import tts
    from yt_video.settings import Settings

    out = tmp_path / "v.mp3"
    with pytest.raises(ValueError, match="empty"):
        tts.generate_voice(text, out, Settings(_env_file=None), {})
    assert not out.exists()
