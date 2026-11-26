"""Logging helpers. Handlers are attached lazily, never at import."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FMT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_DATE_FMT = "%H:%M:%S"
_DEFAULT_NAME = "yt-video"


def get_logger(name: str = _DEFAULT_NAME) -> logging.Logger:
    """Return the logger, installing a console handler on the root app logger once."""
    root = logging.getLogger(_DEFAULT_NAME)
    if not any(getattr(h, "_yt_console", False) for h in root.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
        console._yt_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
        root.setLevel(logging.INFO)
    return logging.getLogger(name)


def add_run_file_handler(log_path: Path) -> logging.Handler:
    """Attach a UTF-8 file handler for one run; caller removes/closes it when done."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
    get_logger().addHandler(handler)
    return handler
