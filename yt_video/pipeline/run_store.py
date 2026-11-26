"""Per-run folders with an atomic ``run.json`` checkpoint."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from yt_video.logging import get_logger
from yt_video.memory._io import write_json_atomic

logger = get_logger()

RunStatus = Literal["running", "awaiting_approval", "done", "abandoned"]
UNFINISHED: tuple[str, ...] = ("running", "awaiting_approval")
RUN_FILE = "run.json"


class RunInProgressError(RuntimeError):
    """Raised when creating a run while another is unfinished."""


class RunStateCorruptError(RuntimeError):
    """A run folder has a run.json that cannot be read as a valid run."""

    def __init__(self, run_dir: Path, reason: str) -> None:
        self.run_dir = Path(run_dir)
        super().__init__(
            f"run state in {self.run_dir} is corrupt ({reason}); repair {RUN_FILE} "
            f"or discard the folder with --restart"
        )


_REQUIRED_KEYS = ("id", "status", "stages")


def _read_run_state(run_file: Path) -> dict[str, Any]:
    """Parse run.json without quarantining; raise RunStateCorruptError if invalid."""
    try:
        with open(run_file, encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunStateCorruptError(run_file.parent, f"unparseable: {exc}") from exc
    if not isinstance(state, dict):
        raise RunStateCorruptError(run_file.parent, f"top level is {type(state).__name__}")
    missing = [k for k in _REQUIRED_KEYS if k not in state]
    if missing:
        raise RunStateCorruptError(run_file.parent, f"missing keys {missing}")
    if not isinstance(state["stages"], dict):
        raise RunStateCorruptError(run_file.parent, "stages is not an object")
    return state


class Run:
    """One pipeline run, persisted in ``<dir>/run.json``."""

    def __init__(self, directory: Path, state: dict[str, Any]) -> None:
        self.dir = Path(directory)
        self._state = state
        self._state.setdefault("stages", {})

    @property
    def id(self) -> str:
        return self._state["id"]

    @property
    def status(self) -> RunStatus:
        return self._state["status"]

    @property
    def slug(self) -> str | None:
        return self._state.get("slug")

    def set_slug(self, slug: str) -> None:
        self._state["slug"] = slug
        self._save()

    def set_status(self, status: RunStatus) -> None:
        self._state["status"] = status
        self._save()

    def is_done(self, stage: str) -> bool:
        return bool(self._state["stages"].get(stage, {}).get("done", False))

    def mark_done(self, stage: str, data: dict[str, Any]) -> None:
        self._state["stages"][stage] = {"done": True, "data": data}
        self._save()

    def save_partial(self, stage: str, data: dict[str, Any]) -> None:
        """Store ``data`` for ``stage`` without marking it done."""
        self._state["stages"][stage] = {"done": False, "data": data}
        self._save()

    def get(self, stage: str) -> dict[str, Any]:
        return self._state["stages"].get(stage, {}).get("data", {})

    def path(self, name: str) -> Path:
        return self.dir / name

    def _save(self) -> None:
        write_json_atomic(self.dir / RUN_FILE, self._state)


class RunStore:
    """Creates, finds and removes runs under ``runs_dir``."""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    def latest_unfinished(self) -> Run | None:
        if not self.runs_dir.is_dir():
            return None
        unfinished: list[Run] = []
        for run_file in self.runs_dir.glob(f"*/{RUN_FILE}"):
            state = _read_run_state(run_file)
            if state["status"] not in UNFINISHED:
                continue
            unfinished.append(Run(run_file.parent, state))
        if not unfinished:
            return None
        unfinished.sort(key=lambda r: r.id)
        if len(unfinished) > 1:
            logger.warning(
                "runs: %d unfinished runs (%s); using newest %s",
                len(unfinished),
                ", ".join(r.id for r in unfinished),
                unfinished[-1].id,
            )
        return unfinished[-1]

    def create(self, now: datetime) -> Run:
        existing = self.latest_unfinished()
        if existing is not None:
            raise RunInProgressError(
                f"run {existing.id} is {existing.status}; resume, abandon or discard it first"
            )
        run_id = now.strftime("%Y-%m-%d_%H%M%S")
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)  # FileExistsError, never overwrite
        run = Run(
            run_dir,
            {
                "id": run_id,
                "status": "running",
                "slug": None,
                "created_at": now.isoformat(),
                "stages": {},
            },
        )
        run._save()
        return run

    def abandon(self, run: Run) -> None:
        run.set_status("abandoned")

    def discard(self, run: Run) -> None:
        """Delete ``run.dir`` only, after verifying it is inside ``runs_dir``."""
        self.discard_dir(run.dir)

    def discard_dir(self, path: Path) -> None:
        """Delete a run folder by path (e.g. one with corrupt run.json), guarded."""
        target = Path(path).resolve()
        root = self.runs_dir.resolve()
        if target == root or root not in target.parents:
            raise ValueError(f"refusing to delete {target}: not inside {root}")
        shutil.rmtree(target)
