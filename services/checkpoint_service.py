"""
checkpoint_service.py — yt-video

Persists pipeline step state to output/pipeline_state.json keyed by date.
Re-running the pipeline resumes from the last incomplete step.
Pass --restart to force a full re-run from scratch.

Identical to v1 pipeline; imports updated for standalone yt-video package.
"""

import json
import os
import glob
from datetime import date
from logger import logger
from yt_video.config import config

# Use centralized state path for yt_video
STATE_FILE = config.YT_PIPELINE_STATE_PATH


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    dirpath = os.path.dirname(STATE_FILE) or "output"
    os.makedirs(dirpath, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_today_state() -> dict:
    """Return today's pipeline state, auto-resetting if it's a new day."""
    state = _load_state()
    today = str(date.today())
    if state.get("date") != today:
        logger.info(f"New day ({today}) — resetting pipeline state")
        state = {"date": today, "steps": {}}
        _save_state(state)
    return state


def is_done(step: str) -> bool:
    state = get_today_state()
    done  = state.get("steps", {}).get(step, {}).get("done", False)
    if done:
        logger.info(f"Step '{step}' already completed today — skipping")
    return done


def mark_done(step: str, **data) -> None:
    """Mark a step as complete and optionally persist result data."""
    state = get_today_state()
    state.setdefault("steps", {})[step] = {"done": True, **data}
    _save_state(state)
    logger.debug(f"Checkpoint saved: step '{step}' marked done")


def get_step_data(step: str) -> dict:
    """Retrieve stored data for a completed step."""
    state = get_today_state()
    return state.get("steps", {}).get(step, {})


def _cleanup_output_files():
    """Delete all generated output files for --restart mode."""
    patterns = [
        "output/script.txt",           # main script
        "output/voice.mp3",            # TTS audio (legacy name)
        "output/*_voice.mp3",          # TTS audio (slug-based)
        "output/final_video.mp4",      # final video (legacy name)
        "output/*_20[0-9][0-9][0-9][0-9][0-9][0-9].mp4",  # final video (slug-based)
        "output/thumbnail.jpg",        # YouTube thumbnail
        "output/transcript.json",      # Whisper transcript (legacy)
        "output/*_transcript.json",    # Whisper transcript (slug-based)
        "output/chart_timeline.json",  # scene timeline (legacy)
        "output/*_timeline.json",      # scene timeline (slug-based)
        "output/scene_*.jpg",          # Pollinations scene images
    ]

    for pattern in patterns:
        files = glob.glob(pattern)
        for filepath in files:
            try:
                os.remove(filepath)
                logger.debug(f"Deleted: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to delete {filepath}: {e}")

    logger.info("Cleaned all generated output files")


def reset_today() -> None:
    """Force-reset today's pipeline state and delete all generated output files (used by --restart flag)."""
    state = {"date": str(date.today()), "steps": {}}
    _save_state(state)
    _cleanup_output_files()
    logger.info("Pipeline state reset and output files cleaned for today")
