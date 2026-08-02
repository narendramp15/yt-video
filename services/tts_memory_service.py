"""
tts_memory_service.py — yt-video

TTS quality feedback loop: learns from each run to make the next one sound better.

How it works
------------
After every run, analyze_and_update() reads the Whisper word-level transcript
and the original script to detect two kinds of quality signals:

  1. Hesitation words — words preceded by an unusually long silence gap (≥ 0.35s).
     These suggest the TTS engine stumbled because the word was hard to pronounce
     (acronyms, compounds, foreign names). Accumulated across runs → pronunciation
     corrections table that _clean_for_tts() applies each time.

  2. Long sentences — sentences with > 20 words. TTS engines rush these and
     listeners lose the thread. Tracked as a rate metric.

Both signals feed into a set of style_notes that are injected into the next
script generation prompt, so the AI progressively writes more TTS-friendly
scripts without any human intervention.

Memory file: output/tts_style_memory.json  (grows across runs, ~5 KB max)
"""

import json
import re
import os
import datetime
from collections import defaultdict
from logger import logger

_MEMORY_PATH  = "output/tts_style_memory.json"
_GAP_THRESHOLD = 0.35   # seconds — gap before a word that flags a hesitation
_LONG_SENT_WORDS = 20   # words — sentences longer than this are "too long"

# ---------------------------------------------------------------------------
# Memory I/O
# ---------------------------------------------------------------------------


def _load() -> dict:
    if os.path.exists(_MEMORY_PATH):
        try:
            with open(_MEMORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "runs": 0,
        "last_updated": "",
        "pronunciation_corrections": {},
        "hesitation_words": {},
        "style_notes": [],
        "stats_history": [],
    }


def _save(mem: dict) -> None:
    os.makedirs("output", exist_ok=True)
    with open(_MEMORY_PATH, "w") as f:
        json.dump(mem, f, indent=2)


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _detect_hesitations(words: list[dict]) -> dict[str, int]:
    """
    Find words preceded by an unusually long silence.
    Returns {word: count} where count = how many times this word caused a gap.
    """
    hesitations: dict[str, int] = defaultdict(int)
    for i in range(1, len(words)):
        prev_end   = words[i - 1].get("end", 0.0)
        curr_start = words[i].get("start", prev_end)
        gap        = curr_start - prev_end
        if gap >= _GAP_THRESHOLD:
            raw = words[i].get("word", "").strip().strip(".,!?;:\"'()")
            if raw and len(raw) >= 3:
                hesitations[raw] += 1
    return dict(hesitations)


def _avg_sentence_length(script: str) -> tuple[float, float]:
    """
    Returns (avg_words_per_sentence, long_sentence_rate).
    long_sentence_rate = fraction of sentences exceeding _LONG_SENT_WORDS.
    """
    # Strip scene markers and speaker labels first
    text = re.sub(r"\[SCENE:[^\]]+\]", "", script)
    text = re.sub(r"^(?:Narrator|Host|Speaker)\s*:\s*", "", text, flags=re.MULTILINE | re.IGNORECASE)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s for s in sentences if len(s.split()) >= 3]
    if not sentences:
        return 0.0, 0.0
    lengths = [len(s.split()) for s in sentences]
    avg    = sum(lengths) / len(lengths)
    long_r = sum(1 for length in lengths if length > _LONG_SENT_WORDS) / len(lengths)
    return round(avg, 1), round(long_r, 2)


def _derive_style_notes(mem: dict) -> list[str]:
    """
    Re-derive the full style_notes list from accumulated stats.
    Called after every update so notes always reflect total history.
    """
    notes: list[str] = []
    runs = mem.get("runs", 0)

    # ── Sentence length ──────────────────────────────────────────────────
    history = mem.get("stats_history", [])
    if history:
        avg_long_rate = sum(h.get("long_sentence_rate", 0) for h in history) / len(history)
        avg_sent_len  = sum(h.get("avg_words_per_sentence", 0) for h in history) / len(history)

        if avg_long_rate > 0.15:
            notes.append(
                f"Keep sentences under {_LONG_SENT_WORDS} words. "
                f"In {runs} past run(s), {avg_long_rate:.0%} of sentences were too long, "
                "causing the TTS to rush and listeners to lose track."
            )
        if avg_sent_len > 18:
            notes.append(
                "Add commas at natural breathing points to help the TTS pace itself. "
                "Short punchy sentences outperform long complex ones for YouTube audiences."
            )

    # ── Hesitation words ────────────────────────────────────────────────
    hesitations = mem.get("hesitation_words", {})
    frequent    = sorted(hesitations.items(), key=lambda x: -x[1])[:8]
    if frequent:
        word_list = ", ".join(f'"{w}"' for w, _ in frequent)
        notes.append(
            f"The TTS engine hesitated before these words in past runs: {word_list}. "
            "Spell them out phonetically or rephrase to avoid pronunciation stumbles."
        )

    # ── General rules (always included after first run) ────────────────
    if runs >= 1:
        notes.extend([
            "Write ONLY spoken narration — no stage directions, no 'Narrator:' labels, "
            "no parenthetical cues.",
            "Sound human: contractions, short punchy lines mixed with longer ones, "
            "occasional rhetorical questions. Never open with canned lines like "
            "'Real data. Verified sources.' or 'In this video we'll…'.",
            "Avoid em-dashes (—) mid-sentence; use commas or full stops instead as "
            "em-dashes cause unnatural TTS pauses.",
            "Spell out acronyms the first time: 'AI (Artificial Intelligence)' then 'AI'. "
            "Avoid ALL-CAPS words except well-known initialisms.",
        ])

    return notes


def _auto_corrections(hesitations: dict[str, int]) -> dict[str, str]:
    """
    Build pronunciation correction mappings for obvious cases:
    - ALL_CAPS acronyms → spaced letters  (API → A P I)
    - CamelCase tech words → spaced       (ChatGPT → Chat G P T)
    - Numbers run together with letters   (GPT4 → GPT 4)
    """
    corrections = {}
    for word in hesitations:
        clean = word.strip(".,!?;:\"'()")
        if not clean:
            continue

        # ALL CAPS acronym (2–6 letters)
        if re.match(r"^[A-Z]{2,6}$", clean):
            spaced = " ".join(list(clean))
            corrections[clean] = spaced

        # Mixed-case tech compound: has digit run-on
        elif re.search(r"[A-Za-z]\d|\d[A-Za-z]", clean):
            spaced = re.sub(r"([A-Za-z])(\d)", r"\1 \2", clean)
            spaced = re.sub(r"(\d)([A-Za-z])", r"\1 \2", spaced)
            corrections[clean] = spaced

    return corrections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_and_update(transcript_path: str, script: str) -> None:
    """
    Read the Whisper transcript, analyze quality signals, and persist
    what was learned to output/tts_style_memory.json.

    Call this once per run, after transcribe_and_sync() completes.

    Parameters
    ----------
    transcript_path : path to Whisper word-level JSON (output/transcript.json)
    script          : the long_script string that was narrated
    """
    mem = _load()

    # Load transcript words
    try:
        with open(transcript_path) as f:
            words = json.load(f)
        if not isinstance(words, list):
            words = []
    except Exception as exc:
        logger.warning(f"tts_memory: could not read transcript: {exc}")
        return

    # Detect hesitation words this run
    new_hesitations = _detect_hesitations(words)
    total_words     = sum(new_hesitations.values())

    # Merge into cumulative hesitation counts
    cumulative: dict[str, int] = mem.get("hesitation_words", {})
    for word, count in new_hesitations.items():
        cumulative[word] = cumulative.get(word, 0) + count
    mem["hesitation_words"] = cumulative

    # Sentence length stats
    avg_sent, long_rate = _avg_sentence_length(script)

    # Auto pronunciation corrections (ALL-CAPS / digit-run-ons only)
    new_corrections = _auto_corrections(new_hesitations)
    existing_corrections: dict = mem.get("pronunciation_corrections", {})
    existing_corrections.update(new_corrections)
    mem["pronunciation_corrections"] = existing_corrections

    # Update stats history
    mem.setdefault("stats_history", []).append({
        "run":                   mem.get("runs", 0) + 1,
        "date":                  datetime.date.today().isoformat(),
        "avg_words_per_sentence": avg_sent,
        "long_sentence_rate":    long_rate,
        "hesitation_words_this_run": new_hesitations,
        "total_hesitation_words":    total_words,
    })
    mem["runs"]         = mem.get("runs", 0) + 1
    mem["last_updated"] = datetime.date.today().isoformat()

    # Re-derive style notes from all accumulated data
    mem["style_notes"] = _derive_style_notes(mem)

    _save(mem)

    logger.info(
        f"tts_memory updated (run #{mem['runs']}): "
        f"{len(new_hesitations)} new hesitation words, "
        f"avg sentence {avg_sent} words, "
        f"{len(new_corrections)} auto-corrections added"
    )
    if new_hesitations:
        top = sorted(new_hesitations.items(), key=lambda x: -x[1])[:5]
        logger.info(f"  Top hesitation words: {top}")


def get_prompt_injection() -> str:
    """
    Return a formatted block to inject into the script generation prompt.
    Empty string if no memory exists yet (first run).
    """
    mem = _load()
    if mem.get("runs", 0) == 0:
        return ""

    notes    = mem.get("style_notes", [])
    corrects = mem.get("pronunciation_corrections", {})
    runs     = mem["runs"]

    lines = [
        f"LEARNED TTS STYLE NOTES (from {runs} previous run(s) — follow strictly):",
    ]
    for note in notes:
        lines.append(f"  • {note}")

    if corrects:
        sample = list(corrects.items())[:6]
        pairs  = ", ".join(f'"{k}"→"{v}"' for k, v in sample)
        lines.append(f"  • Pronounce these correctly: {pairs}")

    return "\n".join(lines) + "\n"


def get_pronunciation_corrections() -> dict[str, str]:
    """
    Return the accumulated {word: replacement} pronunciation table
    for use in _clean_for_tts().
    """
    return _load().get("pronunciation_corrections", {})
