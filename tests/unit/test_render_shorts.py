"""Shorts composite duration policy: audio, background and captions share one capped duration."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from yt_video.media import render


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.zeros(int(seconds * rate), dtype=np.int16).tobytes())
    return path


@pytest.fixture
def images(tmp_path):
    out = {}
    for i in range(2):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (64, 64), (i * 80, 40, 90)).save(p)
        out[f"s{i}"] = str(p)
    return out


CUES = [
    {"text": "early", "start": 0.0, "end": 1.0},
    {"text": "straddles", "start": 3.5, "end": 6.0},
    {"text": "after the cap", "start": 5.0, "end": 7.0},
]


def test_long_voice_truncates_every_layer_at_the_cap(tmp_path, images, caplog):
    voice = _wav(tmp_path / "v.wav", 8.0)
    with caplog.at_level("WARNING"):
        clip = render._build_shorts_composite(voice, images, CUES, max_duration=4.0)
    assert clip.duration == pytest.approx(4.0)
    assert clip.audio.duration == pytest.approx(4.0)
    layers = getattr(clip, "clips", [])
    assert layers and all(c.start + c.duration <= 4.0 + 1e-6 for c in layers)
    assert len(layers) == 3  # background + 2 caption cues (the one starting at 5.0 is dropped)
    assert "8.0s" in caplog.text and "4.0s" in caplog.text


def test_short_voice_keeps_its_own_duration(tmp_path, images):
    voice = _wav(tmp_path / "v.wav", 2.0)
    clip = render._build_shorts_composite(voice, images, [CUES[0]], max_duration=4.0)
    assert clip.duration == pytest.approx(2.0)
    assert clip.audio.duration == pytest.approx(2.0)


def test_cap_constant_is_175_seconds():
    assert render.SHORTS_MAX_DURATION_S == 175.0
