import json
from datetime import date, datetime
from typing import Any, cast

import pytest
from pydantic import BaseModel

from yt_video.pipeline.context import RunContext
from yt_video.pipeline.run_store import Run, RunInProgressError, RunStore
from yt_video.pipeline.runner import Pipeline, StageOutcome

NOW = datetime(2026, 11, 26, 9, 5, 7)


def make_ctx(run: Run) -> RunContext:
    none: Any = None
    return RunContext(
        settings=none, run=run, router=none, images=none, clip=none,
        tts_memory=none, analytics=none, youtube=None, ledger=none,
        no_upload=True, today=date(2026, 11, 26),
    )


class FakeStage:
    def __init__(self, name, outcome=None, fail=False, data=None):
        self.name, self.outcome, self.fail, self.data, self.calls = name, outcome, fail, data, 0

    def run(self, ctx):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        if self.data is not None:
            ctx.run.mark_done(self.name, self.data)
        return self.outcome


def test_create_refuses_when_unfinished(tmp_path):
    store = RunStore(tmp_path / "runs")
    run = store.create(NOW)
    assert run.id == "2026-11-26_090507"
    with pytest.raises(RunInProgressError):
        store.create(datetime(2026, 11, 26, 10, 0, 0))
    store.abandon(run)
    assert store.latest_unfinished() is None
    with pytest.raises(FileExistsError):
        store.create(NOW)


def test_failed_stage_not_marked_done_and_resumes(tmp_path):
    store = RunStore(tmp_path / "runs")
    ctx = make_ctx(store.create(NOW))
    s1, s2, s3 = FakeStage("a"), FakeStage("b", fail=True), FakeStage("c")
    with pytest.raises(RuntimeError):
        Pipeline([s1, s2, s3]).run(ctx)
    assert ctx.run.is_done("a") and not ctx.run.is_done("b")
    resumed = store.latest_unfinished()
    s2.fail = False
    assert Pipeline([s1, s2, s3]).run(make_ctx(resumed)) == "done"
    assert (s1.calls, s2.calls, s3.calls) == (1, 2, 1)
    assert resumed.status == "done"
    assert store.latest_unfinished() is None


def test_wait_sets_awaiting_and_resume_reruns_waiting_stage(tmp_path):
    store = RunStore(tmp_path / "runs")
    ctx = make_ctx(store.create(NOW))
    s1, s2, s3 = FakeStage("a"), FakeStage("b", StageOutcome.WAIT), FakeStage("c")
    assert Pipeline([s1, s2, s3]).run(ctx) == "awaiting_approval"
    assert ctx.run.status == "awaiting_approval"
    assert not ctx.run.is_done("b") and s3.calls == 0
    resumed = store.latest_unfinished()
    assert resumed is not None and resumed.status == "awaiting_approval"
    s2.outcome = None
    assert Pipeline([s1, s2, s3]).run(make_ctx(resumed)) == "done"
    assert (s1.calls, s2.calls, s3.calls) == (1, 2, 1)


def test_runner_marks_done_only_if_stage_did_not(tmp_path):
    store = RunStore(tmp_path / "runs")
    ctx = make_ctx(store.create(NOW))
    Pipeline([FakeStage("a", data={"x": 1}), FakeStage("b")]).run(ctx)
    assert ctx.run.get("a") == {"x": 1}
    assert ctx.run.is_done("b") and ctx.run.get("b") == {}


def test_save_partial_keeps_not_done(tmp_path):
    run = RunStore(tmp_path / "runs").create(NOW)
    run.save_partial("publish", {"video_id": "v1"})
    assert run.get("publish") == {"video_id": "v1"} and not run.is_done("publish")
    reloaded = RunStore(tmp_path / "runs").latest_unfinished()
    assert reloaded.get("publish") == {"video_id": "v1"}


def test_discard_only_deletes_run_dir(tmp_path):
    runs = tmp_path / "runs"
    store = RunStore(runs)
    sibling = runs / "2020-01-01_000000"
    sibling.mkdir(parents=True)
    (sibling / "run.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    run = store.create(NOW)
    store.discard(run)
    assert not run.dir.exists()
    assert sibling.exists() and state.exists()
    outside = Run(tmp_path / "state", {"id": "x", "status": "running"})
    with pytest.raises(ValueError):
        store.discard(outside)
    assert state.exists()


def test_run_json_atomic(tmp_path):
    run = RunStore(tmp_path / "runs").create(NOW)
    run.mark_done("a", {"k": "v"})
    run.set_slug("my-slug")
    data = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert data["stages"]["a"] == {"done": True, "data": {"k": "v"}}
    assert data["slug"] == "my-slug" and data["status"] == "running"
    assert not (run.dir / "run.json.tmp").exists()


def test_latest_unfinished_picks_newest_and_ignores_bare_folders(tmp_path):
    runs = tmp_path / "runs"
    for rid, status in [("2026-01-01_000000", "running"), ("2026-02-01_000000", "awaiting_approval"),
                        ("2026-03-01_000000", "done")]:
        (runs / rid).mkdir(parents=True)
        (runs / rid / "run.json").write_text(json.dumps({"id": rid, "status": status}), encoding="utf-8")
    (runs / "2026-04-01_000000").mkdir()
    assert RunStore(runs).latest_unfinished().id == "2026-02-01_000000"


class M(BaseModel):
    n: int


def test_context_load(tmp_path):
    run = RunStore(tmp_path / "runs").create(NOW)
    ctx = make_ctx(run)
    with pytest.raises(ValueError):
        ctx.load("s", M)
    run.mark_done("s", {"n": 3})
    assert ctx.load("s", M) == M(n=3)
