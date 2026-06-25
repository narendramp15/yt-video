"""
tts_service.py — yt-video  (FREE replacement)

TTS provider chain — best available wins:
  1. Microsoft Edge TTS  (cloud, 100% FREE, 300+ voices)  ← primary
  2. OpenAI tts-1        (cloud, ~$2/month)               ← fallback

Why Edge TTS?
  • Completely free — no API key, no account needed
  • 300+ voices including natural Indian English voices
  • 90% as good as ElevenLabs for finance narration
  • Most viewers won't notice the difference
  • Saves $22–$99/month vs ElevenLabs

Install:  pip install edge-tts

Edge TTS outputs MP3 → saved as output/voice.mp3
generate_voice() always returns the path it actually wrote.
"""

import os
import re
import asyncio
import subprocess
import tempfile
from config import config
from logger import logger
from services.cost_service import record_tts

_OPENAI_TTS_LIMIT = 4096   # OpenAI hard limit per request


# ---------------------------------------------------------------------------
# Audio normalization (-14 LUFS broadcast standard via FFmpeg loudnorm)
# ---------------------------------------------------------------------------
def _normalize_audio(path: str) -> None:
    """
    Normalize audio to -14 LUFS (YouTube/podcast broadcast standard).
    Uses FFmpeg loudnorm filter. Silently skips if FFmpeg is not installed.
    Modifies the file in-place via a temporary file.
    """
    try:
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(path)[1])
        os.close(fd)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", path,
                "-af", "loudnorm=I=-14:TP=-2:LRA=11",
                "-ar", "44100", tmp,
            ],
            check=True,
            capture_output=True,
        )
        os.replace(tmp, path)
        logger.debug(f"Audio normalized to -14 LUFS: {path}")
    except FileNotFoundError:
        logger.debug("FFmpeg not found — skipping audio normalization")
    except subprocess.CalledProcessError as exc:
        logger.warning(f"Audio normalization failed (non-fatal): {exc}")
    except Exception as exc:
        logger.warning(f"Audio normalization unexpected error (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Text normalisation (same as v1)
# ---------------------------------------------------------------------------
def _clean_for_tts(text: str) -> str:
    """
    Normalise text before sending to any TTS engine.
    Replaces symbols/characters that confuse phonemizers.
    Also applies any pronunciation corrections learned from previous runs.
    """
    # Rupee sign → "rupees"
    text = re.sub(r"₹\s*([\d,]+)", lambda m: m.group(1) + " rupees", text)
    # Percent → "percent"
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    # Remove thousands commas in numbers so TTS reads them correctly
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    # Base replacements
    replacements = {
        "NIFTY 50": "Nifty Fifty",
        "NIFTY":    "Nifty",
        "EMA":      "E M A",
        "SMA":      "S M A",
        "SEBI":     "Sebi",
        "NSE":      "N S E",
        "BSE":      "B S E",
        "Q1":  "Q 1", "Q2": "Q 2", "Q3": "Q 3", "Q4": "Q 4",
        "FY":  "Financial Year",
        "YoY": "year over year",
        "QoQ": "quarter over quarter",
        "▲": "up", "▼": "down",
    }
    # Merge in learned pronunciation corrections from previous runs
    try:
        from services.tts_memory_service import get_pronunciation_corrections
        learned = get_pronunciation_corrections()
        if learned:
            replacements.update(learned)
    except Exception:
        pass
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Strip remaining non-ASCII symbols
    text = re.sub(r"[^\x00-\x7F₹]+", " ", text)
    # Collapse extra whitespace
    text = re.sub(r" {2,}", " ", text).strip()
    return text


def _chunk_text(text: str, max_chars: int = _OPENAI_TTS_LIMIT) -> list[str]:
    """Split script into ≤max_chars chunks at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], ""
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


# ---------------------------------------------------------------------------
# Provider 1 — Microsoft Edge TTS  (FREE, primary)
# ---------------------------------------------------------------------------
async def _edge_tts_async(text: str, voice: str, out_path: str) -> None:
    """
    Async helper — Edge TTS is async-native.
    Uses edge_tts.Communicate which streams the audio directly to file.
    """
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "edge-tts not installed. Run: pip install edge-tts"
        )

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)


def _generate_voice_edge(script: str, out_path: str = "output/voice.mp3") -> str:
    """
    Generate speech using Microsoft Edge TTS (free, no API key required).

    Voices for Indian English content:
      en-IN-NeerjaNeural   — female, natural, clear (default)
      en-IN-PrabhatNeural  — male, professional
      en-US-AriaNeural     — female, US English, very natural
      en-GB-SoniaNeural    — female, British English

    Returns: path to the generated MP3 file.
    """
    voice = config.EDGE_TTS_VOICE
    os.makedirs("output", exist_ok=True)

    logger.info(f"Edge TTS: generating voice (voice='{voice}')...")

    # Edge TTS has a ~3000-char soft limit per request; chunk long scripts
    script_clean = _clean_for_tts(script)
    chunks = _chunk_text(script_clean, max_chars=2800)
    logger.info(f"Edge TTS: {len(chunks)} chunk(s) to synthesise")

    if len(chunks) == 1:
        asyncio.run(_edge_tts_async(script_clean, voice, out_path))
    else:
        # Synthesise each chunk to a temp file, then concatenate with pydub
        temp_files: list[str] = []
        for i, chunk in enumerate(chunks):
            tmp = f"output/_edge_chunk_{i}.mp3"
            asyncio.run(_edge_tts_async(chunk, voice, tmp))
            temp_files.append(tmp)

        try:
            from pydub import AudioSegment
            combined = AudioSegment.empty()
            for tf in temp_files:
                combined += AudioSegment.from_mp3(tf)
            combined.export(out_path, format="mp3")
            logger.info(f"Edge TTS: merged {len(temp_files)} chunks → {out_path}")
        except ImportError:
            # pydub not installed — use ffmpeg directly via subprocess
            import subprocess
            concat_list = "output/_edge_concat.txt"
            with open(concat_list, "w") as f:
                for tf in temp_files:
                    f.write(f"file '{os.path.abspath(tf)}'\n")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", concat_list, "-c", "copy", out_path],
                check=True, capture_output=True,
            )
            logger.info(f"Edge TTS: merged {len(temp_files)} chunks via ffmpeg → {out_path}")
        finally:
            for tf in temp_files:
                try:
                    os.remove(tf)
                except OSError:
                    pass

    _normalize_audio(out_path)
    size_kb = os.path.getsize(out_path) // 1024
    logger.info(f"Edge TTS complete — {size_kb} KB → {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Provider 2 — OpenAI tts-1  (cheap cloud fallback, ~$2/month)
# ---------------------------------------------------------------------------
def _generate_voice_openai(script: str, out_path: str = "output/voice.mp3") -> str:
    from openai import OpenAI
    client   = OpenAI(api_key=config.OPENAI_API_KEY)
    chunks   = _chunk_text(script)

    logger.info(f"OpenAI TTS: {len(chunks)} chunk(s) to render")
    parts: list[bytes] = []
    for i, chunk in enumerate(chunks, 1):
        logger.debug(f"  chunk {i}/{len(chunks)} ({len(chunk)} chars)")
        parts.append(
            client.audio.speech.create(
                model="tts-1", voice="alloy", input=chunk
            ).content
        )

    total_chars = sum(len(c) for c in chunks)
    record_tts("yt_video", "openai", "tts-1", total_chars)

    with open(out_path, "wb") as f:
        f.write(b"".join(parts))

    logger.info(f"OpenAI TTS complete ({sum(len(p) for p in parts):,} bytes) → {out_path}")
    _normalize_audio(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_voice(script: str, out_path: str = "output/voice.mp3") -> str:
    """
    Generate voiceover for the given script text.

    Provider priority:
      1. Microsoft Edge TTS  (free, 300+ voices, no API key)
      2. OpenAI TTS tts-1    (fallback, requires OPENAI_API_KEY, ~$2/mo)

    Returns: path to generated MP3 file.
    """
    os.makedirs("output", exist_ok=True)
    script = _clean_for_tts(script)

    # 1. Edge TTS (free)
    try:
        return _generate_voice_edge(script, out_path=out_path)
    except Exception as exc:
        logger.warning(f"Edge TTS failed ({exc}) — falling back to OpenAI TTS...")

    # 2. OpenAI tts-1 (fallback)
    logger.info("Using OpenAI TTS (tts-1) as fallback...")
    return _generate_voice_openai(script, out_path=out_path)


def list_edge_voices() -> None:
    """
    Utility: print all available Edge TTS voices.
    Run: python -c "from services.tts_service import list_edge_voices; list_edge_voices()"
    """
    async def _list():
        import edge_tts
        voices = await edge_tts.list_voices()
        en_voices = [v for v in voices if v["Locale"].startswith("en-")]
        for v in sorted(en_voices, key=lambda x: x["Locale"]):
            print(f"  {v['ShortName']:45s}  {v['Locale']}  ({v['Gender']})")

    asyncio.run(_list())
