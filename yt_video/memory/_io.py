"""Shared atomic JSON I/O for memory files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any | None:
    """Return parsed JSON, or None when the file is missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json_atomic(path: Path, data: Any, ensure_ascii: bool = True) -> None:
    """Write UTF-8 JSON to ``<path>.tmp`` then os.replace; creates the parent dir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
    os.replace(tmp, path)
