# yt-video — Trend-Driven YouTube Pipeline

Automated faceless YouTube channel pipeline. It turns trending topics into a long-form video plus a Short, publishes them, and learns from the results through two feedback loops (TTS quality and YouTube Analytics).

The LLM work runs on your **Claude CLI subscription** (zero marginal cost), with a cheap OpenAI fallback. Voice, images and rendering are free or local.

---

## Quick Start

```bash
# 1. Install dependencies (uv, lockfile-backed)
uv sync

# 2. Configure
cp env.example .env
# Edit .env. If the Claude CLI is logged in, only the REQUIRED block of env.example is needed:
# YOUTUBE_CLIENT_SECRET, CHANNEL_NAME and DEFAULT_CATEGORY.

# 3. Run the pipeline (resumes the unfinished run, if any)
uv run python main.py

# 4. Run the tests (live tests are deselected by default)
uv run pytest -q
```

All commands run from the repository root. `uv run yt-video ...` is equivalent to `uv run python main.py ...`.

Prerequisites: Python 3.11+, `ffmpeg` on PATH, and the `claude` CLI logged in via OAuth. The CLI is invoked with `-p`, never `--bare` (which would disable subscription auth), from an empty temp directory with `--setting-sources ""` so your global CLAUDE.md, hooks and plugins are not loaded.

---

## CLI

| Flag | Effect |
|---|---|
| *(none)* | Resume the unfinished run, or start a new one |
| `--restart` | Discard the unfinished run and start fresh |
| `--new` | Abandon the unfinished run (kept on disk) and start fresh |
| `--no-upload` | Run everything except the YouTube upload |
| `--claude-only` | Use only the Claude CLI provider (no OpenAI fallback) |
| `--fetch-analytics` | Fetch YouTube Analytics for videos uploaded 48+ hours ago |
| `--list-voices` | Print all Edge TTS voices and exit |
| `--list-packaging` | Print the pending packaging variants and exit |
| `--approve-packaging [--variant N]` | Approve pending packaging for public upload (optionally choose the 0-based variant) |
| `--reject-packaging` | Reject packaging; the run uploads as private |
| `--migrate-state` | Copy exactly five legacy v1 files into `state/` and exit: `tts_style_memory.json`, `costs.db`, `review_log.json`, `analytics_log.json`, `x_post_log.json` (never deletes or overwrites) |

Exit codes: `0` done, `1` pipeline error, `2` usage error, `3` **awaiting packaging approval**.

Approval flow (`PACKAGING_REQUIRE_APPROVAL=true` and the review would make the video public): the run stops with exit code 3. Inspect with `--list-packaging`, then `--approve-packaging` (or `--reject-packaging`) and re-run `main.py` to continue.

---

## Architecture: stages + providers

The `yt_video/` package is a linear pipeline of resumable **stages** that call swappable **providers**.

```
yt_video/
  cli.py            argparse entry point (main.py is a thin shim)
  settings.py       typed settings from .env (pydantic-settings)
  costs.py          SQLite cost ledger (state/costs.db) + per-run summary
  llm_tasks.py      LLM-facing tasks (rank topics, write script, review, packaging)
  prompts/          prompt templates (string.Template, $name placeholders)
  pipeline/
    runner.py       runs stages in order, persists after each
    run_store.py    run folders + run.json, single-unfinished-run invariant
    stages/         text.py (topics, script, review) | media.py (voice, images, sync, render) | publish.py
  providers/
    llm/            claude_cli, openai_api, router (+ circuit breaker)
    image/          pollinations, cloudflare, openai_image, placeholder, chain
    tts.py          Edge TTS (OpenAI TTS fallback)
    transcribe.py   faster-whisper (OpenAI Whisper fallback)
  media/            images, CLIP verification, MoviePy/FFmpeg render
  memory/           tts_memory, analytics_memory (the two feedback loops)
  integrations/     trends (pytrends, RSS, yfinance news, X trends), youtube, x
  domain/           pure logic: formats, packaging, captions, sync, topics, text
```

Pipeline flow:

```
Topic discovery   pytrends + RSS + yfinance news + X trends -> niche filter -> LLM rank/score
        |            (YouTube Analytics memory injected from run 2 onward)
Script            one LLM call -> long script with [SCENE:label] markers, Shorts script, SEO
        |            (TTS style memory + analytics notes injected)
Review            LLM compliance score 0-100; public only if score >= 80 AND safety == "Yes"
        |            (anything doubtful is private)
Media             Edge TTS voice -> scene images -> Whisper sync -> captions -> MoviePy render
        |            (long video 1920x1080 + Short, thumbnail 1280x720)
Publish           packaging variants -> optional approval gate -> YouTube upload -> X post
```

Every stage stores its output in `run.json`, so a failed run resumes at the failed stage.

---

## LLM providers

`LLM_PROVIDERS=claude_cli,openai` (ordered).

- **Claude CLI first.** `sonnet` for the script tier, `haiku` for the light tier. Calls are recorded in the ledger at `cost_usd = 0.0`; the envelope's cost is kept as `notional_cost_usd`. Timeouts: `CLAUDE_TIMEOUT_SCRIPT_S` (600) and `CLAUDE_TIMEOUT_LIGHT_S` (180).
- **OpenAI fallback** (`OPENAI_FALLBACK_MODEL=gpt-6-luna`, $0.10 / $0.50 per 1M tokens). `OPENAI_API_KEY` is **optional**: without it the fallback is simply unavailable.
- **Circuit breaker.** The first usage-limit, rate-limit or overage signal from Claude trips the breaker and Claude is skipped for the rest of the run. (Overload/529 and "extra usage required for long context" are not treated as usage limits.)
- **`--claude-only`** removes the OpenAI fallback for that run.
- Structured outputs use `--json-schema` on Claude and strict structured outputs on OpenAI (same schema).

---

## Images

Default chain: `IMAGE_PROVIDERS=pollinations,cloudflare,placeholder`. Providers without credentials are skipped; `placeholder` is an offline gradient, so image generation never fails. OpenAI images are never in the default chain.

| Provider | Notes |
|---|---|
| `pollinations` | Free tier: **1 image / 45 s / IP**, **1024x576 cap**, **logo watermark**. A `POLLINATIONS_KEY` removes all three (the min interval becomes 0). `POLLINATIONS_MIN_INTERVAL_S` sets the free-tier spacing. |
| `cloudflare` | Workers AI free tier. Needs `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. |
| `openai` | Paid; opt-in only (`OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_QUALITY`). |
| `placeholder` | Offline PIL gradient; last resort. |

Because of the free-tier spacing, a 30-scene video spends roughly 20+ minutes on images without a Pollinations key. A provider that keeps returning HTTP errors is disabled for the run, and the run summary reports it.

CLIP verification (`CLIP_VERIFY`, `CLIP_MIN_SCORE=0.24`) scores each image against its scene subject and regenerates off-topic ones. If `torch`/`transformers` are missing, the first valid image is accepted. Install with `uv sync --extra clip` (or `--extra all`).

---

## Script format: [SCENE:label] markers

The script contains markers on their own lines (every 2-4 sentences: `intro`, `s1` ... `sN`, `outro`). They drive image generation and are stripped before TTS and before the review step, so they are never spoken or reviewed as text.

```
[SCENE:intro]
A hundred billion dollars. That's what one Indian company just committed to AI...

[SCENE:s1]
But why now?...
```

---

## Feedback loops

1. **TTS memory** (`state/tts_style_memory.json`). After each run the Whisper word timestamps are scanned for hesitations (gaps >= 0.35 s). It learns pronunciation corrections (`API -> A P I`, `GPT4 -> GPT 4`), sentence-length notes and stumble words, and injects them into the next script prompt.
2. **YouTube Analytics memory** (`state/yt_analytics_memory.json`). `--fetch-analytics` (run 48+ hours after upload) pulls views, retention, watch time, likes and subscribers gained, and learns topic and hook/CTA notes for the next run.

---

## Packaging and publishing

- 3 title/hook variants (`PACKAGING_VARIANTS`), heuristic pick, thumbnail with hook text overlay (`THUMBNAIL_TEXT_ENABLED`), chapters, and an up-next blurb.
- Niche lock (`CHANNEL_PRIMARY_NICHE`, `NICHE_LOCK_ENABLED`) hard-filters topics; it falls back to the unfiltered list if everything is dropped.
- Shorts (`SHORTS_ENABLED`): audio, background and captions share one capped duration (175 s).
- X posting after a public upload (`X_*` keys, `X_DAILY_POST_LIMIT`, `X_POST_PUBLIC_ONLY`).

Background music: drop tracks you may use commercially into `assets/music/` (`.mp3 .wav .m4a .aac .ogg .flac`). The first one is used, or set `BACKGROUND_MUSIC_TRACK`.

---

## Run folders and state

Per-run artifacts live in `output/runs/<YYYY-MM-DD>_<HHMMSS>/` (git-ignored via `output/`):

| File | Description |
|---|---|
| `run.json` | Stage results, slug, privacy decision; the resume source of truth |
| `packaging_pending.json` | Packaging awaiting approval |
| `<slug>.mp4`, `<slug>_shorts.mp4` | Rendered videos |
| voice, images, thumbnail, captions, transcript | Stage artifacts |
| run log | Full log for that run |

Durable cross-run state lives in `state/` (git-ignored):

| File | Description |
|---|---|
| `costs.db` | SQLite cost ledger (real and notional costs) |
| `tts_style_memory.json` | TTS feedback loop memory |
| `yt_analytics_memory.json` | Analytics feedback loop memory |
| `analytics_log.json` | Post-upload analytics snapshots |
| `review_log.json` | Review log (newline-delimited JSON) |
| `x_post_log.json` | X posting log (daily cap) |
| `youtube_token.json` | Cached OAuth token (Data + Analytics scopes) |
| `playlists.json` | Playlist cache |

Upgrading from v1: run `uv run python main.py --migrate-state` once to copy the five legacy files listed above from `output/` into `state/`. `yt_analytics_memory.json`, the OAuth token and the playlist cache are not copied (the token is re-created on first sign-in).

Only one unfinished run exists at a time; use `--new` or `--restart` to move on.

---

## Environment variables

`env.example` is the authoritative, commented list and mirrors `yt_video/settings.py`. Highlights:

| Variable | Default | Description |
|---|---|---|
| `YOUTUBE_CLIENT_SECRET` | `client_secret.json` | Google OAuth client JSON |
| `CHANNEL_NAME` | none | Channel name (tags) |
| `LLM_PROVIDERS` | `claude_cli,openai` | LLM order |
| `CLAUDE_MODEL_SCRIPT` / `CLAUDE_MODEL_LIGHT` | `sonnet` / `haiku` | Claude model aliases |
| `CLAUDE_TIMEOUT_SCRIPT_S` / `CLAUDE_TIMEOUT_LIGHT_S` | `600` / `180` | Per-call timeouts |
| `OPENAI_API_KEY` | none (optional) | Fallback LLM, OpenAI TTS/Whisper/images |
| `OPENAI_FALLBACK_MODEL` | `gpt-6-luna` | Fallback model |
| `IMAGE_PROVIDERS` | `pollinations,cloudflare,placeholder` | Image chain |
| `POLLINATIONS_MODEL` / `POLLINATIONS_KEY` / `POLLINATIONS_MIN_INTERVAL_S` | `flux` / none / `45` | Pollinations |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | none | Cloudflare Workers AI |
| `EDGE_TTS_VOICE` / `EDGE_TTS_RATE` / `EDGE_TTS_PITCH` | `en-IN-NeerjaNeural` / `-8%` / `+1Hz` | Voice |
| `PACKAGING_REQUIRE_APPROVAL` | `false` | Gate public uploads (exit code 3) |
| `CHANNEL_PRIMARY_NICHE` | none | Comma-separated niche keywords |
| `STATE_DIR` / `RUNS_DIR` | `state` / `output/runs` | Storage locations |

---

## Optional extras

```bash
uv sync --extra whisper   # faster-whisper local transcription (~145 MB base model)
uv sync --extra clip      # torch + transformers for CLIP verification (~600 MB)
uv sync --extra all
```

Without faster-whisper, transcription falls back to the OpenAI Whisper API (needs `OPENAI_API_KEY`).

---

## Testing

```bash
uv run pytest -q                 # unit + provider tests; no network, no real claude binary
uv run pytest -m live            # real Claude CLI / image provider calls (opt-in)
```

Layout: `tests/unit`, `tests/providers`, `tests/live` (marker `live`, deselected by default).
