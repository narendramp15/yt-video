"""Voiceover: Edge TTS (free) with OpenAI TTS as a paid, ledgered fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yt_video.costs import CostLedger
    from yt_video.settings import Settings

log = logging.getLogger(__name__)

OPENAI_TTS_MODEL = "tts-1"
OPENAI_TTS_VOICE = "alloy"
OPENAI_TTS_USD_PER_M_CHARS = 15.00
_OPENAI_TTS_LIMIT = 4096
_EDGE_CHUNK_CHARS = 2800

_BASE_REPLACEMENTS = {
    "NIFTY 50": "Nifty Fifty",
    "NIFTY": "Nifty",
    "EMA": "E M A",
    "SMA": "S M A",
    "SEBI": "Sebi",
    "NSE": "N S E",
    "BSE": "B S E",
    "Q1": "Q 1", "Q2": "Q 2", "Q3": "Q 3", "Q4": "Q 4",
    "FY": "Financial Year",
    "YoY": "year over year",
    "QoQ": "quarter over quarter",
    "▲": "up", "▼": "down",
}


def apply_corrections(text: str, corrections: dict[str, str]) -> str:
    """Replace whole words only (case-sensitive); longer keys are applied first."""
    for key in sorted(corrections, key=len, reverse=True):
        if not key:
            continue
        value = corrections[key]
        text = re.sub(rf"\b{re.escape(key)}\b", lambda _m, v=value: v, text)
    return text


def _clean_for_tts(text: str) -> str:
    text = re.sub(r"₹\s*([\d,]+)", lambda m: m.group(1) + " rupees", text)
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    for k, v in _BASE_REPLACEMENTS.items():
        text = text.replace(k, v)
    text = text.replace("—", ", ").replace("–", ", ")
    text = text.replace("...", ".")
    text = re.sub(r"[^\x00-\x7F₹]+", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _chunk_text(text: str, max_chars: int = _OPENAI_TTS_LIMIT) -> list[str]:
    """Split text into chunks of at most ``max_chars`` at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            while len(sentence) > max_chars:
                chunks.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _normalize_prosody(value: str, unit: str) -> str:
    """Accept bare numbers ("-8") or suffixed values ("-8%"); empty -> +0<unit>."""
    raw = (value or "").strip()
    if not raw:
        return f"+0{unit}"
    if raw[-1].isdigit():
        if not raw.startswith(("+", "-")):
            raw = f"+{raw}"
        return f"{raw}{unit}"
    return raw


def _normalize_audio(path: Path) -> None:
    """Normalize to -14 LUFS via ffmpeg loudnorm. Non-fatal if unavailable."""
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(suffix=path.suffix)
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-af", "loudnorm=I=-14:TP=-2:LRA=11",
             "-ar", "44100", tmp],
            check=True, capture_output=True,
        )
        os.replace(tmp, path)
    except FileNotFoundError:
        log.debug("ffmpeg not found; skipping audio normalization")
    except Exception as exc:  # non-fatal
        log.warning("Audio normalization failed (non-fatal): %s", exc)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


async def _edge_save(text: str, voice: str, out: Path, rate: str, pitch: str) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))


def _concat_mp3(parts: list[Path], out_path: Path) -> None:
    concat_list = out_path.with_suffix(".concat.txt")
    try:
        concat_list.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )
    finally:
        concat_list.unlink(missing_ok=True)


def _generate_edge(text: str, out_path: Path, settings: Settings) -> None:
    voice = settings.edge_tts_voice
    rate = _normalize_prosody(settings.edge_tts_rate, "%")
    pitch = _normalize_prosody(settings.edge_tts_pitch, "Hz")
    chunks = _chunk_text(text, max_chars=_EDGE_CHUNK_CHARS)
    log.info("Edge TTS: voice=%s rate=%s pitch=%s chunks=%d", voice, rate, pitch, len(chunks))
    if not chunks:
        raise ValueError("empty text after cleaning")
    if len(chunks) == 1:
        asyncio.run(_edge_save(chunks[0], voice, out_path, rate, pitch))
        return
    parts = [out_path.with_name(f"{out_path.stem}._chunk{i}.mp3") for i in range(len(chunks))]
    try:
        for chunk, part in zip(chunks, parts):
            asyncio.run(_edge_save(chunk, voice, part, rate, pitch))
        _concat_mp3(parts, out_path)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)


def _generate_openai(
    text: str,
    out_path: Path,
    settings: Settings,
    ledger: CostLedger | None,
    run_id: str,
    fallback_reason: str,
) -> None:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    chunks = _chunk_text(text)
    log.info("OpenAI TTS: %d chunk(s)", len(chunks))
    parts = [
        client.audio.speech.create(
            model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=chunk
        ).content
        for chunk in chunks
    ]
    out_path.write_bytes(b"".join(parts))
    if ledger is not None:
        chars = sum(len(c) for c in chunks)
        ledger.record(
            run_id, "voice", "tts", "openai", OPENAI_TTS_MODEL, None, None,
            chars * OPENAI_TTS_USD_PER_M_CHARS / 1_000_000,
            fallback_reason=fallback_reason,
        )


def generate_voice(
    text: str,
    out_path: Path,
    settings: Settings,
    corrections: dict[str, str],
    ledger: CostLedger | None = None,
    run_id: str = "",
) -> Path:
    """Synthesize ``text`` to ``out_path`` (Edge TTS, then OpenAI TTS) and return it."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _clean_for_tts(apply_corrections(text, corrections))

    try:
        _generate_edge(cleaned, out_path, settings)
    except Exception as exc:
        log.warning("Edge TTS failed (%s); falling back to OpenAI TTS", exc)
        _generate_openai(cleaned, out_path, settings, ledger, run_id, "edge_tts_failed")
    _normalize_audio(out_path)
    return out_path


def list_edge_voices() -> None:
    """Print all English Edge TTS voices."""

    async def _list() -> None:
        import edge_tts

        voices = await edge_tts.list_voices()
        for v in sorted((v for v in voices if v["Locale"].startswith("en-")),
                        key=lambda x: x["Locale"]):
            print(f"  {v['ShortName']:45s}  {v['Locale']}  ({v['Gender']})")

    asyncio.run(_list())
