"""Copy-only migration of v1 learned memory and logs into the durable state/ folder."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from yt_video.logging import get_logger

log = get_logger(__name__)

_SQLITE_MAGIC = b"SQLite format 3\x00"

# target name -> legacy source dirs, relative to repo_root (first existing wins)
_SOURCES: dict[str, tuple[str, ...]] = {
    "tts_style_memory.json": ("output",),
    "costs.db": ("output", "../output"),
    "review_log.json": ("output",),
    "analytics_log.json": ("output",),
    "x_post_log.json": ("output", "../output"),
}


@dataclass
class MigrationReport:
    copied: list[str] = field(default_factory=list)
    skipped_missing: list[str] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)
    skipped_exists: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def needs_migration(state_dir: Path) -> bool:
    return not (state_dir / "tts_style_memory.json").exists() and not (state_dir / "costs.db").exists()


def _is_valid(name: str, path: Path) -> bool:
    try:
        if name.endswith(".db"):
            with path.open("rb") as fh:
                return fh.read(16) == _SQLITE_MAGIC
        text = path.read_text(encoding="utf-8")
        if name == "review_log.json":  # v1 wrote newline-delimited JSON
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
            return True
        json.loads(text)
        return True
    except (OSError, ValueError):
        return False


def _copy_atomic(name: str, source: Path, target: Path) -> None:
    """Copy via a temp file then os.replace, so a partial copy never lands on the target.

    .db files use the sqlite backup API (reads committed WAL content, source opened read-only).
    """
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        if name.endswith(".db"):
            with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as src, closing(
                sqlite3.connect(tmp)
            ) as dst:
                src.backup(dst)
        else:
            shutil.copy2(source, tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def migrate_state(state_dir: Path, repo_root: Path) -> MigrationReport:
    """Copy legacy files into ``state_dir``. Never deletes, modifies or overwrites anything."""
    report = MigrationReport()
    state_dir.mkdir(parents=True, exist_ok=True)
    for name, rel_dirs in _SOURCES.items():
        target = state_dir / name
        if target.exists():
            report.skipped_exists.append(name)
            continue
        source = next(
            (c for c in ((repo_root / rel / name) for rel in rel_dirs) if c.is_file()), None
        )
        if source is None:
            report.skipped_missing.append(name)
            continue
        if not _is_valid(name, source):
            log.warning("Skipping invalid legacy file %s (%s)", name, source)
            report.skipped_invalid.append(name)
            continue
        try:
            _copy_atomic(name, source, target)
        except (OSError, sqlite3.Error) as exc:
            log.warning("Failed to migrate %s from %s: %s", name, source, exc)
            report.failed.append(name)
            continue
        report.copied.append(name)
    return report
