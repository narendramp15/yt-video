"""CLI tests: no network, no real claude binary, no real providers."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime

import pytest

from yt_video import cli
from yt_video.cli import main
from yt_video.pipeline.run_store import RunStore
from yt_video.pipeline.runner import Pipeline
from yt_video.settings import PROJECT_ROOT

VARIANTS = [
    {"title": "Title A", "hook": "HOOK A HERE", "variant_id": "v1", "score": 5.0},
    {"title": "Title B With Number 12", "hook": "NUMBER HIT NOW", "variant_id": "v2", "score": 8.0},
    {"title": "Title C", "hook": "HOOK C HERE", "variant_id": "v3", "score": 6.0},
]


def _pending(variants=VARIANTS) -> dict:
    return {
        "topic": "AI chips",
        "angle": "tech",
        "variants": variants,
        "winner": variants[0],
        "slug": "ai-chips",
        "format_id": "numbers",
        "approved": False,
        "rejected": False,
        "chosen_index": None,
    }


@pytest.fixture(autouse=True)
def _offline(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_log_claude_version", lambda s: None)
    monkeypatch.setattr(cli, "build_router", lambda *a, **k: object())
    monkeypatch.setattr(cli, "build_image_chain", lambda *a, **k: object())


@pytest.fixture
def store(settings) -> RunStore:
    return RunStore(settings.runs_dir)


@pytest.fixture
def pending_run(store):
    run = store.create(datetime(2026, 1, 1, 10, 0, 0))
    run.set_status("awaiting_approval")
    path = run.path("packaging_pending.json")
    path.write_text(json.dumps(_pending()), encoding="utf-8")
    return run


def _read(run) -> dict:
    return json.loads(run.path("packaging_pending.json").read_text(encoding="utf-8"))


def test_approve_without_run_exits_2(capsys):
    assert main(["--approve-packaging"]) == 2


def test_reject_without_run_exits_2():
    assert main(["--reject-packaging"]) == 2


def test_approve_bad_variant_exits_2(pending_run):
    before = pending_run.path("packaging_pending.json").read_bytes()
    assert main(["--approve-packaging", "--variant", "5"]) == 2
    assert main(["--approve-packaging", "--variant", "-1"]) == 2
    assert pending_run.path("packaging_pending.json").read_bytes() == before


def test_approve_reject_roundtrip(pending_run):
    pending = _read(pending_run)
    assert pending["approved"] is False

    assert main(["--approve-packaging", "--variant", "1"]) == 0
    approved = _read(pending_run)
    assert approved["approved"] is True
    assert approved["rejected"] is False
    assert approved["chosen_index"] == 1
    assert approved["variants"][approved["chosen_index"]]["variant_id"] == "v2"

    assert main(["--reject-packaging"]) == 0
    rejected = _read(pending_run)
    assert rejected["rejected"] is True
    assert rejected["approved"] is False


def test_approve_without_variant_keeps_index_none(pending_run):
    assert main(["--approve-packaging"]) == 0
    data = _read(pending_run)
    assert data["approved"] is True and data["chosen_index"] is None


def test_list_packaging_prints_indices(pending_run, capsys):
    assert main(["--list-packaging"]) == 0
    out = capsys.readouterr().out
    assert "[0] v1" in out and "[2] v3" in out


def test_variant_requires_approve():
    assert main(["--variant", "1"]) == 2


def test_exclusive_packaging_flags():
    assert main(["--approve-packaging", "--reject-packaging"]) == 2


def test_corrupt_run_on_packaging_command_exits_2(settings):
    bad = settings.runs_dir / "2026-01-01_000000"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{not json", encoding="utf-8")
    assert main(["--approve-packaging"]) == 2
    assert bad.exists()


def test_corrupt_run_default_exits_2_and_restart_discards(settings, monkeypatch):
    bad = settings.runs_dir / "2026-01-01_000000"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: "done")
    assert main([]) == 2
    assert bad.exists()
    assert main(["--restart"]) == 0
    assert not bad.exists()


def test_awaiting_returns_3(monkeypatch, capsys):
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: "awaiting_approval")
    assert main([]) == 3
    assert "--approve-packaging" in capsys.readouterr().out


def test_done_prints_summary_and_returns_0(monkeypatch, capsys):
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: "done")
    assert main(["--no-upload", "--claude-only"]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_pipeline_error_returns_1(monkeypatch):
    def boom(self, ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(Pipeline, "run", boom)
    assert main([]) == 1


def test_default_run_resumes_unfinished(store, monkeypatch):
    run = store.create(datetime(2026, 1, 1, 10, 0, 0))
    seen = []
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: seen.append(ctx.run.id) or "done")
    assert main([]) == 0
    assert seen == [run.id]


def test_restart_discards_unfinished(store, monkeypatch):
    old = store.create(datetime(2026, 1, 1, 10, 0, 0))
    seen = []
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: seen.append(ctx.run.id) or "done")
    assert main(["--restart"]) == 0
    assert not old.dir.exists()
    assert seen and seen[0] != old.id


def test_new_abandons_unfinished(store, monkeypatch):
    old = store.create(datetime(2026, 1, 1, 10, 0, 0))
    seen = []
    monkeypatch.setattr(Pipeline, "run", lambda self, ctx: seen.append(ctx.run.id) or "done")
    assert main(["--new"]) == 0
    assert old.dir.exists()
    assert json.loads(old.path("run.json").read_text(encoding="utf-8"))["status"] == "abandoned"
    assert seen[0] != old.id


def test_migrate_state_flag(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(cli, "migrate_state", lambda state_dir, root: calls.append(state_dir))
    assert main(["--migrate-state"]) == 0
    assert calls == [settings.state_dir]


def test_list_voices(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "list_edge_voices", lambda: called.append(1))
    assert main(["--list-voices"]) == 0
    assert called


def test_fetch_analytics(monkeypatch):
    monkeypatch.setattr(cli, "YouTubeClient", lambda *a, **k: type("Y", (), {"credentials": lambda s: None})())
    monkeypatch.setattr(
        cli, "AnalyticsMemory",
        lambda *a, **k: type("A", (), {"fetch_pending": lambda s: 2})(),
    )
    assert main(["--fetch-analytics"]) == 0


def test_main_py_shim():
    proc = subprocess.run(
        ["uv", "run", "python", "main.py", "--variant", "1"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 2, proc.stderr
