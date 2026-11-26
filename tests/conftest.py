import os

import pytest

from yt_video.settings import Settings, load_settings


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    """Legacy root tests import config.py, which loads the real .env into os.environ."""
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        state_dir=tmp_path / "state",
        runs_dir=tmp_path / "runs",
        image_cache_dir=tmp_path / "cache" / "images",
    )
