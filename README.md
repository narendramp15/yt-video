# yt-video — 100% Free Trend-Driven YouTube Pipeline

Automated faceless YouTube channel pipeline that turns **trending topics** into polished YouTube videos — with zero paid API dependencies and two self-improving feedback loops that get better with every run.

> **Separate from `yt_ai_factory/`** — that pipeline handles NIFTY 50 candlestick-chart videos. This pipeline is for general trending-topic content only.

---

## Cost Comparison

| Component | Original (Paid) | yt-video (Free) | Monthly Saving |
|---|---|---|---|
| AI Voiceover | ElevenLabs `$22–$99/mo` | **Microsoft Edge TTS** (300+ voices, no key) | **$22–$99** |
| Scene Illustrations | DALL-E / Flux API `$3–$12/mo` | **Pollinations.ai** (no API key needed) | **$3–$12** |
| Serverless Rendering | AWS Lambda `$5–$20/mo` | **Local machine / GitHub Actions** (free CI) | **$5–$20** |
| Trend & News Signals | NewsAPI `$49/mo` | **pytrends + Reddit PRAW + RSS** (all free) | **$49** |
| **Total** | **$79–$180/month** | **~$0.07/month** (LLM script generation only) | **$79–$180** |

---

## Quick Start

```bash
# 1. Install dependencies (uv — fast, lockfile-backed)
uv sync

# 2. Configure environment
cp yt_video/env.example .env
# Edit .env — only OPENAI_API_KEY + YOUTUBE_CLIENT_SECRET are required

# 3. Run the pipeline
uv run python yt_video/main.py

# 4. Force a full re-run (clear today's checkpoint)
uv run python yt_video/main.py --restart

# 5. List all available Edge TTS voices
uv run python yt_video/main.py --list-voices

# 6. Fetch YouTube Analytics 48h+ after upload (improves next run)
uv run python yt_video/main.py --fetch-analytics
```

Or run directly from inside `yt_video/`:

```bash
cd yt_video
uv run python main.py
uv run python main.py --restart
uv run python main.py --fetch-analytics
```

---

## Full Pipeline Architecture

```
Phase 1 — Trend Discovery
  pytrends (Google Trends India)
  + Reddit PRAW (r/IndiaInvestments, r/technology, r/india)
  + RSS feeds (yfinance news, general)
        |
        v
  AI filter + virality score  <-- YouTube Analytics memory injected (run 2+)
        |
        v
  Top 3 ranked topics -> pick #1 (highest score)

Phase 2 — Script Generation  (one mega-prompt AI call)
  Topic + angle + trend signals
  + TTS style memory (learned from past runs)      <-- TTS feedback loop
  + Analytics performance notes (retention, CTR)  <-- Analytics feedback loop
        |
        v
  [LONG_SCRIPT]     — 8–12 min narration with [SCENE:label] visual markers
  [SHORTS_SCRIPT]   — 50–60 sec punchy version
  [SEO_TITLE]       — under 70 chars, high CTR
  [SEO_DESCRIPTION] — 400–480 chars with CTA
  [SEO_TAGS]        — 10 tags (broad + specific)

Phase 3 — Media Assets  (all free)
  Edge TTS         ->  output/voice.mp3
  Pollinations.ai  ->  one image per [SCENE:label] (24-36 dense scene beats)
                        topic-aware prompts + unique seeds -> visually distinct scenes
  CLIP verify      ->  scores each image vs its scene subject; regenerates
                        off-topic images with a fresh seed (content-to-scene gate)
                        + thumbnail (1280x720)
  faster-whisper   ->  word timestamps + narration-word scene alignment

Phase 4 — Render  (local FFmpeg via MoviePy)
  Scene images + voice + optional bg music  ->  output/final_video.mp4  (1920x1080)

Phase 5 — Review & Publish
  AI score gate       ->  score >= 80 -> public, else private
  YouTube Data API v3 ->  upload with full SEO metadata + thumbnail
  record_video()      ->  saves metadata for 48h analytics fetch
  _log_analytics()    ->  output/analytics_log.json (immediate snapshot)

48h+ later: python main.py --fetch-analytics
  YouTube Analytics API v2  ->  views, retention %, watch time, likes, subs gained
  yt_analytics_memory.json  ->  topic + script notes injected into next run
```

Checkpoint: `output/pipeline_state.json` — re-run resumes from the failed step automatically.

## Optional Royalty-Free Background Music

Drop one or more tracks you have the right to use into `assets/music/` at the
project root. The renderer will automatically use the first supported audio file
it finds there. To force one exact track, set `BACKGROUND_MUSIC_TRACK` in `.env`.

Supported extensions: `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac`.

Example:

```bash
BACKGROUND_MUSIC_ENABLED=true
BACKGROUND_MUSIC_DIR=assets/music
# BACKGROUND_MUSIC_TRACK=assets/music/calm-bed.mp3
```

For original locally generated music, use `tools/generate_music_audiocraft.py`.
It is kept out of the main runtime because AudioCraft usually needs a separate
Python 3.11/3.12 environment with Torch installed.

---

## Pipeline Checkpoint Steps

| Step | Key | What happens |
|---|---|---|
| 1 | `fetch_signals` | pytrends + Reddit + RSS → raw topic list |
| 2 | `filter_topics` | AI batch call → top 3 ranked + scored topics |
| 3 | `generate_script` | Mega-prompt → ScriptOutput (long + shorts + SEO) |
| 4 | `review_script` | AI compliance score 0–100 |
| 5 | `generate_voice` | Edge TTS → `output/voice.mp3` |
| 6 | `generate_images` | Pollinations.ai → `output/images/<scene>_scene.jpg` |
| 7 | `sync_scenes` | Whisper → `output/chart_timeline.json` + `output/transcript.json` |
| 8 | `create_video` | MoviePy + FFmpeg → `output/final_video.mp4` |
| 9 | `upload_video` | YouTube API → video ID recorded for analytics |

---

## Project Structure

```
yt_video/
├── main.py                              # CLI: --restart, --fetch-analytics, --list-voices
├── config.py                            # All config via .env (absolute path, override=True)
├── logger.py                            # Structured logger -> output/pipeline.log
├── requirements.txt
├── env.example
├── services/
│   ├── topic_service.py                 # pytrends + Reddit PRAW + RSS; filter_and_score_topics()
│   ├── script_service.py                # Mega-prompt -> ScriptOutput dataclass; [SCENE:] markers
│   ├── tts_service.py                   # Edge TTS (free) -> OpenAI fallback; applies learned corrections
│   ├── tts_memory_service.py            # TTS feedback loop — learns from Whisper transcript each run
│   ├── image_service.py                 # Pollinations.ai; per-scene prompts + CLIP verify/regenerate
│   ├── clip_service.py                  # Local CLIP image-vs-scene relevance scoring
│   ├── sync_service.py                  # faster-whisper / OpenAI Whisper; narration-word scene alignment
│   ├── video_service.py                 # MoviePy + FFmpeg; Ken Burns on scene images
│   ├── review_service.py                # AI compliance review (0–100 score)
│   ├── checkpoint_service.py            # Step-level persistent checkpoint
│   ├── youtube_service.py               # YouTube Data API v3 upload + thumbnail
│   └── yt_analytics_memory_service.py   # Analytics feedback loop — fetch, learn, inject
└── workflows/
    └── long_video.py                    # Main orchestrator — 5-phase, 9-step pipeline
```

---

## Self-Improving Feedback Loops

### Loop 1 — TTS Voice Quality (`tts_memory_service.py`)

Runs after every pipeline execution. Reads the Whisper word-level transcript to find **hesitation words** — words preceded by >=0.35s silence, meaning Edge TTS stumbled on them.

**What it learns across runs:**

| Signal | Action |
|---|---|
| ALL-CAPS acronyms that caused gaps (`API`, `IPO`) | Auto-adds `API -> A P I` to pronunciation corrections table |
| Digit run-ons (`GPT4`, `AI2025`) | Auto-adds `GPT4 -> GPT 4` |
| Long sentences (>20 words, >15% rate) | Injects "keep sentences under 20 words" note into next script prompt |
| Frequent stumble words | Listed in prompt: *"TTS hesitated before 'ChatGPT' — spell phonetically"* |

Memory file: `output/tts_style_memory.json`

### Loop 2 — YouTube Analytics Performance (`yt_analytics_memory_service.py`)

Fetches real viewer behaviour 48h after upload via YouTube Analytics API v2.

**Metrics fetched:** `views`, `averageViewPercentage` (retention %), `averageViewDuration`, `likes`, `subscribersGained`, `estimatedMinutesWatched`

**What it learns:**

| Signal | Action |
|---|---|
| Topics with >=45% retention | Bias topic scoring toward similar topic keywords |
| Topics with <30% retention | Penalise those keyword categories in scoring |
| Low like-to-view ratio | Adds mid-video CTA instruction to script prompt |
| Zero subscribers gained | Adds specific subscribe reason to script outro instructions |
| Channel avg retention trend | Adjusts hook urgency instruction in script prompt |

Memory file: `output/yt_analytics_memory.json`

```bash
# Run 48+ hours after any upload
uv run python yt_video/main.py --fetch-analytics
```

---

## Script Format — [SCENE:label] Markers

The AI generates scripts with visual cue markers on their own lines. A marker is
placed **every 2–4 sentences** — 24–36 per video — so each image covers one idea,
not a whole chapter. Labels run `intro`, `s1`, `s2` … `sN`, `outro`:

```
[SCENE:intro]
A hundred billion dollars. That's what one Indian company just committed to AI...

[SCENE:s1]
But why now? And why Reliance?...

[SCENE:s2]
Their refinery business alone funds the entire bet...

[SCENE:outro]
The wave is coming. Whether it lifts all boats or washes away the unprepared...
```

- `strip_scene_markers()` removes them before TTS — they are **never spoken**
- `transcribe_and_sync()` aligns each scene's first narration words against the
  Whisper word stream to find exactly where that scene starts in the audio
- `generate_scene_images_for_scenes()` generates one Pollinations image per scene
  from the scene's specific subject, then **CLIP-verifies** it matches and
  regenerates off-topic images with a fresh seed

---

## Free Components — Details

### Microsoft Edge TTS (replaces ElevenLabs)

No API key, no account. Saves $22–$99/month.

```
EDGE_TTS_VOICE=en-IN-NeerjaNeural   # female Indian English (default)
EDGE_TTS_VOICE=en-IN-PrabhatNeural  # male Indian English
```

```bash
uv run python yt_video/main.py --list-voices   # see all 300+ voices
```

### Pollinations.ai (replaces DALL-E / Flux)

No API key needed. Each scene gets a unique prompt built from the scene's specific
subject, position-based visual style, and a different seed per scene.

Fallback chain: Pollinations -> PIL gradient (always works offline).

```
POLLINATIONS_MODEL=flux           # default
POLLINATIONS_MODEL=flux-realism   # more photorealistic
POLLINATIONS_KEY=your_key         # optional: removes rate limits
```

### CLIP content-to-scene verification

Every generated image is scored for semantic relevance to its scene subject
using a local CLIP model (`openai/clip-vit-base-patch32`, CPU, free). Images
below the score threshold are regenerated with a fresh seed (up to 3 tries);
the best attempt is kept. Each run logs a video-level accuracy summary:

```
CLIP scene accuracy: mean 0.291 over 31 scenes, 28/31 (90%) cleared threshold 0.24
```

Requires `torch` + `transformers` (installed by `uv sync`). If absent, the
pipeline runs unchanged — the first valid image is accepted and a warning logged.

```
CLIP_VERIFY=1        # 1 = verify+regenerate (default), 0 = disable
CLIP_MIN_SCORE=0.24  # accept threshold (cosine similarity)
CLIP_MODEL=openai/clip-vit-base-patch32
```

### pytrends + Reddit PRAW (replaces NewsAPI)

**pytrends** — Google Trends India, no key needed.

**Reddit PRAW** setup:
1. https://www.reddit.com/prefs/apps → Create App → Script
2. Add to `.env`:
   ```
   REDDIT_CLIENT_ID=your_id
   REDDIT_CLIENT_SECRET=your_secret
   REDDIT_USER_AGENT=yt-video/1.0
   ```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | Script generation + Whisper API fallback |
| `YOUTUBE_CLIENT_SECRET` | Yes | `client_secret.json` | Path to Google OAuth JSON (auto-resolved to project root) |
| `CHANNEL_NAME` | Yes | — | Your YouTube channel name |
| `SCRIPT_MODEL` | — | `gpt-4o-mini` | `gpt-4o-mini` · `deepseek-chat` · `deepseek-reasoner` |
| `DEEPSEEK_API_KEY` | — | — | Required if `SCRIPT_MODEL=deepseek-*` |
| `EDGE_TTS_VOICE` | — | `en-IN-NeerjaNeural` | Edge TTS voice ID |
| `POLLINATIONS_MODEL` | — | `flux` | Pollinations image model |
| `POLLINATIONS_KEY` | — | — | Optional auth key (removes rate limits) |
| `CLIP_VERIFY` | — | `1` | `1` = CLIP image verification on, `0` = off |
| `CLIP_MIN_SCORE` | — | `0.24` | Image-vs-scene accept threshold (cosine similarity) |
| `CLIP_MODEL` | — | `openai/clip-vit-base-patch32` | HuggingFace CLIP model id |
| `REDDIT_CLIENT_ID` | — | — | Reddit PRAW app ID |
| `REDDIT_CLIENT_SECRET` | — | — | Reddit PRAW app secret |
| `REDDIT_USER_AGENT` | — | `yt-video/1.0` | Reddit API user agent |
| `DEFAULT_CATEGORY` | — | `25` | YouTube category ID (25 = News & Politics) |

---

## Optional: DeepSeek (cheaper AI)

```
SCRIPT_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-...
```

~$0.01/month vs ~$0.06/month with gpt-4o-mini. Same quality for most topics.

---

## Optional: faster-whisper (local transcription)

```bash
uv add faster-whisper   # ~500 MB (ctranslate2)
```

Falls back to OpenAI Whisper API automatically if not installed.

First run downloads model weights to a local cache (one-time):
faster-whisper `base` ~145 MB, CLIP model ~600 MB.

---

## Output Files

| File | Description |
|---|---|
| `output/script.txt` | Generated narration script |
| `output/voice.mp3` | Edge TTS voiceover |
| `output/images/<scene>_scene.jpg` | Pollinations scene backgrounds |
| `output/images/thumbnail.jpg` | YouTube thumbnail (1280×720) |
| `output/chart_timeline.json` | Scene label → start timestamp (seconds) |
| `output/transcript.json` | Whisper word-level timestamps |
| `output/final_video.mp4` | Finished 1080p video |
| `output/review_log.json` | AI compliance review log |
| `output/pipeline_state.json` | Checkpoint state (resume on failure) |
| `output/pipeline.log` | Full run log |
| `output/tts_style_memory.json` | TTS feedback loop memory (grows across runs) |
| `output/yt_analytics_memory.json` | Analytics feedback loop memory |
| `output/analytics_log.json` | Immediate post-upload analytics snapshot |

---

## GitHub Actions (Scheduled CI)

Workflow: `.github/workflows/yt-video-pipeline.yml` — runs Mon–Fri 06:30 IST automatically.

**Secrets** (Settings → Secrets → Actions):

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | or `DEEPSEEK_API_KEY` |
| `YOUTUBE_CLIENT_SECRET_JSON` | Paste full contents of `client_secret.json` |
| `REDDIT_CLIENT_ID` | Optional |
| `REDDIT_CLIENT_SECRET` | Optional |

**Variables** (Settings → Variables → Actions, optional):

```
SCRIPT_MODEL=gpt-4o-mini
EDGE_TTS_VOICE=en-IN-NeerjaNeural
POLLINATIONS_MODEL=flux
```
