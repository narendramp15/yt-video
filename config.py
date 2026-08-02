"""
config.py — yt-video (100% free pipeline)

All secrets loaded from .env via python-dotenv.
Cost breakdown vs original:
  ElevenLabs ($22-99/mo)  → REMOVED  (replaced by edge-tts, free)
  DALL-E / Flux ($3-12/mo) → REMOVED  (replaced by Pollinations.ai, free)
  NewsAPI ($49/mo)         → REMOVED  (replaced by pytrends + RSS feeds + yfinance news, free)
  AWS Lambda ($5-20/mo)    → REMOVED  (local + GitHub Actions)

Required .env keys:
  OPENAI_API_KEY        — script generation + Whisper fallback
  YOUTUBE_CLIENT_SECRET — path to client_secret.json (default: client_secret.json)
  CHANNEL_NAME          — your YouTube channel name

Optional .env keys:
  SCRIPT_MODEL          — gpt-4o-mini (default) | deepseek-chat | deepseek-reasoner
  DEEPSEEK_API_KEY      — only needed when SCRIPT_MODEL starts with "deepseek"
  EDGE_TTS_VOICE        — Microsoft Edge TTS voice (default: en-IN-NeerjaNeural)
  EDGE_TTS_RATE         — speaking rate, e.g. -8% (slightly slower = more human)
  EDGE_TTS_PITCH        — pitch shift, e.g. +1Hz
  POLLINATIONS_MODEL    — image generation model: flux-realism (default) | flux | turbo
  LOWER_THIRDS_ENABLED  — scene label lower-thirds (default: false — less template look)
  SHORTS_ENABLED        — render + upload Shorts from shorts_script (default: true)
  BURN_CAPTIONS_ENABLED — burn Whisper captions into long-form MP4 (default: true)
  CHANNEL_PRIMARY_NICHE — comma keywords for topic niche fit (optional)
  PACKAGING_VARIANTS    — title/thumb A/B variant count (default: 3)
"""

import os
from pathlib import Path
from dotenv import load_dotenv


def _env_flag(name: str, default: bool = True) -> bool:
  raw = os.getenv(name)
  if raw is None:
    return default
  return raw.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_project_path(raw: str | None) -> str | None:
  if not raw:
    return None
  path = Path(raw)
  return str(path if path.is_absolute() else _PROJECT_ROOT / path)

# Project root = directory containing this file (yt-video/).
# Resolved at import time using __file__ so os.chdir() in main.py doesn't affect it.
_PROJECT_ROOT = Path(__file__).parent

# Shared persistent state directory for memory/db files (project-root/state)
STATE_DIR = _PROJECT_ROOT / "state"

# Always load .env from the project root (absolute path), regardless of CWD.
load_dotenv(_PROJECT_ROOT / ".env", override=True)

_DEFAULT_CLIENT_SECRET = str(_PROJECT_ROOT / "client_secret.json")

# Resolve YOUTUBE_CLIENT_SECRET: if the .env value is a relative path,
# anchor it to the project root so it works regardless of CWD.
_raw_secret = os.getenv("YOUTUBE_CLIENT_SECRET", _DEFAULT_CLIENT_SECRET)
_RESOLVED_CLIENT_SECRET = (
    str(_PROJECT_ROOT / _raw_secret) if not os.path.isabs(_raw_secret) else _raw_secret
)


class Config:
    # ── Core (required) ──────────────────────────────────────────────────
    OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY")
    YOUTUBE_CLIENT_SECRET = _RESOLVED_CLIENT_SECRET
    CHANNEL_NAME          = os.getenv("CHANNEL_NAME")
    # Fallback only — packaging_service.resolve_category_id() maps per-topic.
    # 22 = People & Blogs (safer default than always News & Politics).
    DEFAULT_CATEGORY      = os.getenv("DEFAULT_CATEGORY", "22")

    # ── Video packaging / retention craft ────────────────────────────────
    # Full-screen chapter cards every scene break kill watch time — off by default.
    CHAPTER_CARDS_ENABLED = _env_flag("CHAPTER_CARDS_ENABLED", False)
    # Intro card at t=0 fights the spoken hook — off by default.
    INTRO_CARD_ENABLED = _env_flag("INTRO_CARD_ENABLED", False)
    # Lower-thirds every scene look template/AI — off by default.
    LOWER_THIRDS_ENABLED = _env_flag("LOWER_THIRDS_ENABLED", False)
    # Overlay 3–5 word hook text on thumbnails (Pillow) for mobile CTR.
    THUMBNAIL_TEXT_ENABLED = _env_flag("THUMBNAIL_TEXT_ENABLED", True)
    # Phase B — burn Whisper captions into long-form (mute retention).
    BURN_CAPTIONS_ENABLED = _env_flag("BURN_CAPTIONS_ENABLED", True)
    # Phase B — render + upload Shorts cutdown from shorts_script.
    SHORTS_ENABLED = _env_flag("SHORTS_ENABLED", True)
    # Phase B — optional niche keywords for topic re-rank (comma-separated).
    CHANNEL_PRIMARY_NICHE = [
        p.strip().lower()
        for p in os.getenv("CHANNEL_PRIMARY_NICHE", "").split(",")
        if p.strip()
    ]
    # Phase B — how many title/hook packages to generate for heuristic A/B.
    PACKAGING_VARIANTS = max(1, int(os.getenv("PACKAGING_VARIANTS", "3")))

    # ── Script generation ─────────────────────────────────────────────────
    # gpt-4o-mini (~$0.06/mo) | deepseek-chat (~$0.01/mo) | deepseek-reasoner
    SCRIPT_MODEL   = os.getenv("SCRIPT_MODEL", "gpt-4o-mini")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

    # ── FREE TTS — Microsoft Edge TTS ────────────────────────────────────
    # 300+ voices, completely free, no API key needed
    # Indian voices: en-IN-NeerjaNeural (female) | en-IN-PrabhatNeural (male)
    # Full list: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
    EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "en-IN-NeerjaNeural")
    # Slightly slower + tiny pitch lift reads less robotic than stock defaults.
    EDGE_TTS_RATE  = os.getenv("EDGE_TTS_RATE", "-8%")
    EDGE_TTS_PITCH = os.getenv("EDGE_TTS_PITCH", "+1Hz")

    # ── Image Generation — pluggable provider chain ──────────────────────
    # IMAGE_PROVIDERS is an ordered, comma-separated chain tried per image;
    # the first provider that returns a valid image wins. The PIL gradient is
    # always the final offline fallback, so the pipeline never hard-fails.
    #   pollinations  — FREE, no key (default)
    #   openai        — PAID (gpt-image-1 / DALL-E); needs OPENAI_API_KEY
    # Examples:
    #   IMAGE_PROVIDERS=pollinations            # free only (default)
    #   IMAGE_PROVIDERS=openai,pollinations     # paid OpenAI first, free fallback
    # NOTE: DeepSeek has NO image-generation API, so it cannot be an image
    # provider here. To add another service (Stability, Gemini, …) register a
    # `_fetch_<name>` function in image_service._IMAGE_PROVIDERS.
    IMAGE_PROVIDERS = [
        p.strip().lower()
        for p in os.getenv("IMAGE_PROVIDERS", "pollinations").split(",")
        if p.strip()
    ]

    # Pollinations provider (free). Set POLLINATIONS_KEY for authenticated
    # access (removes rate limits). Get key at: https://enter.pollinations.ai/
    # Models: flux-realism (default — less AI-art look), flux, turbo
    POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "flux-realism")
    POLLINATIONS_KEY   = os.getenv("POLLINATIONS_KEY")

    # OpenAI image provider (paid) — only used when 'openai' is in IMAGE_PROVIDERS.
    # Reuses OPENAI_API_KEY. quality is model-specific:
    #   gpt-image-1 → low | medium | high | auto      dall-e-3 → standard | hd
    OPENAI_IMAGE_MODEL   = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
    OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "low")

    # ── Optional background music (royalty-free / CC0 tracks) ──────────────
    # Drop tracks into assets/music/ or point BG_MUSIC_TRACK at one file.
    BACKGROUND_MUSIC_ENABLED = _env_flag("BACKGROUND_MUSIC_ENABLED", True)
    BACKGROUND_MUSIC_TRACK = _resolve_project_path(os.getenv("BACKGROUND_MUSIC_TRACK"))
    BACKGROUND_MUSIC_DIR = _resolve_project_path(os.getenv("BACKGROUND_MUSIC_DIR", "assets/music"))

    # ── X / Twitter Trends ───────────────────────────────────────────────
    # Optional: enables X trending topics as an additional signal source.
    # Get a Bearer Token at: https://developer.twitter.com/en/portal/dashboard
    # Free tier is write-only; Basic ($100/mo) or Academic gives read access.
    # If not set, X trends are silently skipped.
    TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
    # WOEID for location — 23424848 = India, 1 = Worldwide
    # Find WOEIDs: http://woeid.rosselliot.co.nz/
    TWITTER_WOEID = int(os.getenv("TWITTER_WOEID", "23424848"))

    # ── X / Twitter Posting ──────────────────────────────────────────────
    # Optional: post one announcement after a successful public YouTube upload.
    # Uses app/user keys with X API v2 write endpoint.
    X_CONSUMER_KEY = os.getenv("X_CONSUMER_KEY")
    X_CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET")
    X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
    X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")
    X_DAILY_POST_LIMIT = int(os.getenv("X_DAILY_POST_LIMIT", "3"))
    X_POST_PUBLIC_ONLY = os.getenv("X_POST_PUBLIC_ONLY", "true").lower() not in {"0", "false", "no", "off"}

    # Shared persistent state directory (project-root/state)
    STATE_DIR = STATE_DIR

    # Paths for persistent memory files (stored under project-root/state)
    YT_PIPELINE_STATE_PATH = str(STATE_DIR / "yt_pipeline_state.json")
    YT_ANALYTICS_MEMORY_PATH = str(STATE_DIR / "yt_analytics_memory.json")


config = Config()
