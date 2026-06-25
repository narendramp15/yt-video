"""
sync_service.py — yt-video

Builds a scene timeline (label → start_seconds) from voice + script.

Two strategies (both always run, second refines the first):
  PRIMARY  — Script text-length proportional timing (always works, zero deps)
  REFINE   — faster-whisper / OpenAI Whisper word timestamps (optional, improves accuracy)

Script uses [SCENE:label] markers.  These map to the Pollinations images.

Transcription backend:
  1. faster-whisper  (local, free, CPU int8)     install: pip install faster-whisper
  2. OpenAI Whisper API  (fallback, ~$0.006/min)
"""

import re
import json
import os
from config import config
from logger import logger
from services.cost_service import record_whisper

# ---------------------------------------------------------------------------
# Optional: faster-whisper local model
# ---------------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel
    _HAS_FASTER_WHISPER = True
    logger.debug("faster-whisper available (local transcription)")
except ImportError:
    _HAS_FASTER_WHISPER = False
    logger.debug("faster-whisper not installed — will use OpenAI Whisper API")

_fw_model = None   # lazy singleton


def _get_fw_model() -> "WhisperModel":
    global _fw_model
    if _fw_model is None:
        logger.info("Loading faster-whisper model (base, CPU int8)...")
        _fw_model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("faster-whisper model ready")
    return _fw_model


# ---------------------------------------------------------------------------
# Transcription backends
# ---------------------------------------------------------------------------
def _transcribe_local(voice_path: str) -> list[dict]:
    model = _get_fw_model()
    logger.info(f"faster-whisper: transcribing {voice_path}...")
    segments, info = model.transcribe(voice_path, word_timestamps=True)

    words = []
    for segment in segments:
        for w in (segment.words or []):
            words.append({"word": w.word, "start": w.start, "end": w.end})

    logger.info(
        f"faster-whisper: {len(words)} words  "
        f"(language={info.language}, duration={info.duration:.1f}s)"
    )
    return words


def _transcribe_openai(voice_path: str) -> list[dict]:
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    logger.info(f"OpenAI Whisper: transcribing {voice_path}...")
    with open(voice_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = transcript.words or []
    logger.info(f"OpenAI Whisper: {len(words)} words")
    _dur = getattr(transcript, "duration", None) or (words[-1]["end"] if words else 0)
    record_whisper("yt_video", float(_dur) / 60)
    return [{"word": w.word, "start": w.start, "end": w.end} for w in words]


def _get_words(voice_path: str) -> list[dict]:
    if _HAS_FASTER_WHISPER:
        try:
            return _transcribe_local(voice_path)
        except Exception as exc:
            logger.warning(f"faster-whisper failed ({exc}) — falling back to OpenAI Whisper API")
    return _transcribe_openai(voice_path)


# ---------------------------------------------------------------------------
# Strategy 1: Proportional timing from script text length (PRIMARY)
# ---------------------------------------------------------------------------
def _proportional_timeline(script: str, audio_duration: float) -> dict[str, float]:
    """
    Compute scene start times proportional to each scene's text length.
    Uses [SCENE:label] markers from the mega-prompt output.
    Reliable — depends only on script structure, not audio analysis.
    """
    parts = re.split(r"\[SCENE:([^\]]+)\]", script)
    scenes: list[tuple[str, int]] = []
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        text  = parts[i + 1].strip() if i + 1 < len(parts) else ""
        scenes.append((label, max(len(text), 1)))

    if not scenes:
        return {}

    total_chars = sum(n for _, n in scenes)
    timeline: dict[str, float] = {}
    current = 0.0
    for label, chars in scenes:
        timeline[label] = round(current, 2)
        current += (chars / total_chars) * audio_duration

    logger.info("Proportional scene timeline:")
    for label, t in timeline.items():
        logger.info(f"  {label}: {t:.1f}s")
    return timeline


# ---------------------------------------------------------------------------
# Strategy 2: Whisper refinement (OPTIONAL, improves accuracy)
# ---------------------------------------------------------------------------
_ANCHOR_LEN = 6          # narration words used to locate each scene start
_SEARCH_SPAN = 4000      # how far ahead in the transcript to look for a scene


def _norm_token(s: str) -> str:
    """Lowercase and strip everything but letters/digits — for word matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _scenes_with_text(script: str) -> list[tuple[str, str]]:
    """Ordered [(label, narration_text)] from [SCENE:label] markers."""
    parts = re.split(r"\[SCENE:([^\]]+)\]", script)
    scenes: list[tuple[str, str]] = []
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        text  = parts[i + 1].strip() if i + 1 < len(parts) else ""
        scenes.append((label, text))
    return scenes


def _refine_with_whisper(
    timeline: dict[str, float],
    words: list[dict],
    script: str,
) -> dict[str, float]:
    """
    Refine the proportional timeline using actual Whisper word timestamps.

    For each scene we take its first few NARRATION words (the words the
    narrator actually speaks — not the [SCENE:label] marker, which is silent)
    and locate that word sequence in the transcript. The scene's start time
    becomes the timestamp of that matched word. Scenes are matched in order,
    each search starting after the previous match, so timing stays monotonic.

    Falls back to the proportional value for any scene that cannot be matched
    confidently.
    """
    refined = dict(timeline)
    scenes  = _scenes_with_text(script)
    if not scenes:
        return refined

    # Flatten transcript into (normalized_token, start_time), dropping empties.
    tw: list[tuple[str, float]] = []
    for w in words:
        nt = _norm_token(w["word"])
        if nt:
            tw.append((nt, w["start"]))
    if not tw:
        return refined

    search_from = 0
    prev_start  = 0.0

    for label, text in scenes:
        if label not in refined:
            continue

        anchors = [t for t in (_norm_token(x) for x in text.split()) if t][:_ANCHOR_LEN]
        if len(anchors) < 2:
            continue

        # Slide over the transcript; score each position by how many anchor
        # words appear in order within a short window starting there.
        span      = len(anchors) + 4
        limit     = min(len(tw), search_from + _SEARCH_SPAN)
        best_idx  = -1
        best_hits = 0
        for p in range(search_from, limit):
            window = [tok for tok, _ in tw[p:p + span]]
            hits, wi = 0, 0
            for a in anchors:
                while wi < len(window):
                    if window[wi] == a:
                        hits += 1
                        wi += 1
                        break
                    wi += 1
            if hits > best_hits:
                best_hits, best_idx = hits, p
                if hits == len(anchors):
                    break

        # Require a solid majority of anchor words to match before trusting it.
        if best_idx < 0 or best_hits < max(2, (len(anchors) + 1) // 2):
            logger.debug(f"  {label}: no confident whisper match — keeping proportional")
            continue

        candidate = round(tw[best_idx][1], 2)
        if candidate < prev_start:
            logger.debug(f"  {label}: whisper match {candidate:.1f}s precedes previous scene — skipped")
            continue

        logger.debug(f"  {label}: whisper refine {refined[label]:.1f}s → {candidate:.1f}s")
        refined[label] = candidate
        prev_start     = candidate
        search_from    = best_idx + 1

    return refined


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def transcribe_and_sync(
    voice_path: str,
    script: str,
    transcript_path: str = "output/transcript.json",
    timeline_path: str = "output/chart_timeline.json",
) -> dict[str, float]:
    """
    Build scene timeline from voice path and structured script.

    Returns { scene_label: start_seconds } dict.
    Also saves to transcript_path and timeline_path.
    """
    from moviepy import AudioFileClip

    audio_duration = AudioFileClip(voice_path).duration
    logger.info(f"Audio duration: {audio_duration:.1f}s")

    # Step 1: reliable proportional baseline
    timeline = _proportional_timeline(script, audio_duration)

    # Step 2: optionally refine with Whisper
    try:
        words    = _get_words(voice_path)
        timeline = _refine_with_whisper(timeline, words, script)

        # Save transcript for debugging
        os.makedirs("output", exist_ok=True)
        with open(transcript_path, "w") as f:
            json.dump(words, f, indent=2)

    except Exception as exc:
        logger.warning(f"Whisper refinement skipped ({exc}) — using proportional timing")

    # Save final timeline
    os.makedirs("output", exist_ok=True)
    with open(timeline_path, "w") as f:
        json.dump(timeline, f, indent=2)

    logger.info(f"Chart timeline saved → {timeline_path}")
    return timeline
