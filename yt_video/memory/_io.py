"""Shared atomic JSON I/O for memory files."""

from __future__ import annotations

import datetime
import json
import os
import shutil
from pathlib import Path
from typing import Any

from yt_video.logging import get_logger

logger = get_logger()


def read_json(path: Path, expect: type = dict) -> Any | None:
    """Return parsed JSON of type ``expect``, or None when missing or corrupt.

    Corrupt content (bad JSON, bad encoding, wrong top-level type) is copied to
    ``<name>.corrupt-<timestamp>`` and logged; the original stays in place.
    Other OSErrors (locks, permissions) are logged and re-raised so a transient
    read failure can never be turned into an overwrite with defaults.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _quarantine(path, f"unparseable ({exc})")
        return None
    except OSError as exc:
        logger.error(f"memory: cannot read {path}: {exc}")
        raise
    if not isinstance(data, expect):
        _quarantine(path, f"expected {expect.__name__}, got {type(data).__name__}")
        return None
    return data


def _quarantine(path: Path, reason: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        logger.error(f"memory: could not back up {path}: {exc}")
        raise
    logger.warning(f"memory: {path} is {reason}; copied to {backup}, starting from defaults")


def write_json_atomic(path: Path, data: Any, ensure_ascii: bool = True) -> None:
    """Write UTF-8 JSON to ``<path>.tmp`` then os.replace; creates the parent dir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
