"""Word timestamps: local faster-whisper first, OpenAI Whisper as a ledgered fallback."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from yt_video.logging import get_logger

if TYPE_CHECKING:
    from yt_video.costs import CostLedger
    from yt_video.settings import Settings

log = get_logger(__name__)

WHISPER_MODEL = "whisper-1"
WHISPER_USD_PER_MIN = 0.006

_fw_model: Any = None  # lazy module-level singleton


def _get_fw_model() -> Any:
    global _fw_model
    if _fw_model is None:
        from faster_whisper import WhisperModel

        log.info("Loading faster-whisper model (base, CPU int8)...")
        _fw_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _fw_model


def _transcribe_local(voice_path: Path) -> list[dict]:
    model = _get_fw_model()
    segments, info = model.transcribe(str(voice_path), word_timestamps=True)
    words = [
        {"word": w.word, "start": w.start, "end": w.end}
        for segment in segments
        for w in (segment.words or [])
    ]
    log.info("faster-whisper: %d words (duration=%.1fs)", len(words), info.duration)
    return words


def _transcribe_openai(
    voice_path: Path,
    settings: Settings,
    ledger: CostLedger | None,
    run_id: str,
    fallback_reason: str,
) -> list[dict]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    with open(voice_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = transcript.words or []
    if ledger is not None:
        duration = getattr(transcript, "duration", None) or (words[-1].end if words else 0)
        ledger.record(
            run_id, "transcribe", "transcribe", "openai", WHISPER_MODEL, None, None,
            float(duration) / 60 * WHISPER_USD_PER_MIN,
            fallback_reason=fallback_reason,
        )
    return [{"word": w.word, "start": w.start, "end": w.end} for w in words]


def transcribe_words(
    voice_path: Path,
    settings: Settings,
    ledger: CostLedger | None = None,
    run_id: str = "",
) -> list[dict]:
    """Return ``[{"word", "start", "end"}, ...]`` for the audio file."""
    voice_path = Path(voice_path)
    try:
        return _transcribe_local(voice_path)
    except ImportError:
        reason = "faster_whisper_unavailable"
        log.warning("faster-whisper not installed; using OpenAI Whisper")
    except Exception as exc:
        reason = type(exc).__name__
        log.warning("faster-whisper failed (%s); using OpenAI Whisper", exc)
    return _transcribe_openai(voice_path, settings, ledger, run_id, reason)
