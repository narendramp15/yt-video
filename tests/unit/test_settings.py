from pathlib import Path

from yt_video.settings import PROJECT_ROOT, Settings, load_settings


def test_defaults(tmp_path):
    s = load_settings(env_file=tmp_path / "missing.env")
    assert s.llm_providers == ["claude_cli", "openai"]
    assert s.claude_model_script == "sonnet" and s.claude_model_light == "haiku"
    assert s.openai_fallback_model == "gpt-6-luna"
    assert s.image_providers == ["pollinations", "cloudflare", "placeholder"]
    assert s.pollinations_model == "flux"  # flux-realism now 402s on the keyless tier
    assert s.pollinations_min_interval_s == 45
    assert s.openai_api_key is None
    assert s.openai_pricing["gpt-6-luna"] == (0.10, 0.50)


def test_comma_lists_and_override(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDERS=openai\nIMAGE_PROVIDERS=placeholder\n", encoding="utf-8")
    s = load_settings(env_file=env)
    assert s.llm_providers == ["openai"] and s.image_providers == ["placeholder"]


def test_import_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib

    import yt_video.settings as m

    importlib.reload(m)
    assert not (tmp_path / "state").exists()


def test_niche_list_and_paths(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "CHANNEL_PRIMARY_NICHE=Cricket, IPL ,\nCLAUDE_CLI_PATH=/x/claude\n"
        "YOUTUBE_CLIENT_SECRET=secrets/cs.json\n",
        encoding="utf-8",
    )
    s = load_settings(env_file=env)
    assert s.channel_primary_niche == ["cricket", "ipl"]
    assert s.claude_cli_path == "/x/claude"
    assert s.youtube_client_secret == PROJECT_ROOT / "secrets" / "cs.json"
    assert s.state_dir == PROJECT_ROOT / "state"
    assert s.runs_dir == PROJECT_ROOT / "output" / "runs"
    assert s.image_cache_dir == PROJECT_ROOT / "output" / "cache" / "images"
    assert s.claude_timeout_script_s == 600 and s.claude_timeout_light_s == 180


def test_overrides_win(tmp_path):
    s = load_settings(env_file=tmp_path / "missing.env", state_dir=tmp_path / "st")
    assert s.state_dir == Path(tmp_path / "st")


def test_empty_env_values_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", "")
    monkeypatch.setenv("RUNS_DIR", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.state_dir != PROJECT_ROOT and s.runs_dir != PROJECT_ROOT
    assert s.state_dir.name == "state" and s.openai_api_key is None
