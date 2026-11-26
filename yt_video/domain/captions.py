"""Caption cues and SRT output from word-level timestamps."""

from __future__ import annotations

import json
from pathlib import Path

from yt_video.logging import get_logger

logger = get_logger(__name__)


def _srt_timestamp(seconds: float) -> str:
    ms_total = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(ms_total, 3_600_000)
    mins, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def group_caption_cues(
    words: list[dict],
    max_chars: int = 32,
    max_duration: float = 2.5,
) -> list[dict]:
    """Group word-level timestamps into short caption cues: [{text, start, end}, ...]."""
    cues: list[dict] = []
    if not words:
        return cues

    buf: list[str] = []
    start = None
    end = None

    def _flush() -> None:
        nonlocal buf, start, end
        if not buf or start is None or end is None:
            buf, start, end = [], None, None
            return
        text = " ".join(buf).strip()
        if text:
            cues.append({"text": text, "start": float(start), "end": float(end)})
        buf, start, end = [], None, None

    for w in words:
        token = str(w.get("word", "")).strip()
        if not token:
            continue
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", w_start))
        candidate = (" ".join(buf + [token])).strip()
        duration = (w_end - start) if start is not None else 0.0
        overflow = buf and (len(candidate) > max_chars or duration > max_duration)
        if overflow:
            _flush()
        if start is None:
            start = w_start
        buf.append(token)
        end = w_end

    _flush()
    return cues


def words_to_srt(words: list[dict], out_path: Path) -> Path:
    """Write an SRT file from word timestamps. Returns ``out_path``."""
    out_path = Path(out_path)
    cues = group_caption_cues(words)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, cue in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(cue['start'])} --> {_srt_timestamp(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"SRT written ({len(cues)} cues) -> {out_path}")
    return out_path


def load_caption_cues(transcript_path: Path) -> list[dict]:
    """Load a word-timestamp JSON file and return grouped caption cues ([] on any problem)."""
    if not transcript_path or not Path(transcript_path).exists():
        return []
    try:
        words = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        if not isinstance(words, list):
            return []
        return group_caption_cues(words)
    except Exception as exc:
        logger.warning(f"Could not load caption cues from {transcript_path}: {exc}")
        return []
