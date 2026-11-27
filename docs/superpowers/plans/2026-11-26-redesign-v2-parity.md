# v2 parity run results

Run ID: `2026-11-27_095259` (resumed several times after fixes; folder `output/runs/2026-11-27_095259/`, not committed).
Topic picked: "Mr. IPL" (Suresh Raina), format `what_changed`, title "Suresh Raina's Greatest IPL Records, Moments & Why Fans Love Him". Command: `uv run python main.py --no-upload` (final resume with a `POLLINATIONS_MODEL=flux` env override; `.env` untouched). Final exit code 0.

## Steps

| Step | Result |
|---|---|
| 0. `uv sync --extra whisper` | Done (faster-whisper local). torch/CLIP not installed. |
| 1. `pytest -m live` | Pass (2): Claude structured call, Pollinations. No OpenAI live test exists. |
| 2. `main.py --no-upload` | Pass after fixes (exit 0). Folder holds `<slug>.mp4`, `<slug>_shorts.mp4`, `<slug>.srt`, `<slug>_shorts.srt`, `thumbnail.jpg`, `pipeline.log`. LLM rows are `claude_cli` throughout. |
| 3. Mechanical checks | Pass with caveats, see below. |
| 4. `LLM_PROVIDERS=openai --new` | Skipped: `OPENAI_API_KEY` in `.env` returns 401; owner to replace key. |
| 5. `--fetch-analytics` | Skipped: no YouTube token; owner to run interactively. |

### Run summary (final resume)
```
rank_topics   claude_cli  claude-haiku-5-5   $0.000000
write_script  claude_cli  claude-sonnet-5-5  $0.000000
packaging     claude_cli  claude-haiku-5-5   $0.000000
review        claude_cli  claude-haiku-5-5   $0.000000
images        claude_cli  claude-sonnet-5-5  $0.000000  (x3)
TOTAL $0.000000   Claude input tokens: 28232   Claude notional cost: $0.2115
Images: pollinations 28
```
The cost ledger has no TTS, transcribe or image rows (all free or local). Total real spend: $0.

### Elapsed time (wall clock, includes failed attempts and waits)
- Whole effort 09:52 to 14:10. Final resume 10:44 to 14:10 (3 h 26 min): images 52 min (Pollinations free tier, 1 image / 45 s, plus 503s), transcribe 3 min, long render 2 h 14 min (15468 frames at about 2 fps), Shorts render 16 min.
- Earlier stages: rank 16 s, script 2.5 min, packaging 10 s, review 15 s, voice (Edge TTS) 2.5 min.

### Mechanical video check (ffprobe / ffmpeg; frames sampled at 20/50/80 %)
| Artifact | Result |
|---|---|
| Long mp4 | h264 1920x1080 + aac, 645.59 s. Voice mp3 645.55 s (diff 0.04 s). Burned captions visible in frames. |
| Shorts mp4 | h264 1080x1920 + aac, **94.46 s**. Captions visible, but the frame at 80 % is solid black (Defect D8). |
| Thumbnail | The run's `thumbnail.jpg` was 1024x576 (Pollinations cap). Fixed in code (fix 6); regenerated from cache into scratch: 1280x720 with hook text "MR IPL DECODED". |
| Scene images | 27 of 27 labels from pollinations, 1024x576 native (free-tier cap), upscaled in render. A "pollinations.ai" logo is baked into the bottom-right corner (free tier ignores `nologo=true`); visible in video frames and the thumbnail. A Pollinations key is needed. |

## Section 6.9 checklist (Reddit excluded per Decision 4)

| Bullet | Status | Evidence |
|---|---|---|
| Trend sources: pytrends, RSS, X trends scrape, yfinance news | Partial | Log: trends24.in 25 topics; RSS 5 headlines; yfinance 0 headlines; Google Trends (pytrends) 429, logged as WARNING and skipped. Combined 32 signals. |
| LLM topic ranking, rerank, niche lock, recent-topic dedupe | Pass (ran) | `rank_topics` on claude_cli/haiku; selected "Mr. IPL", score 8.0. Rerank internals covered by unit tests only. |
| Format pick + format-to-playlist mapping | Pass | run.json: `format_id=what_changed`, `playlist_title="What Changed This Week"`. Playlist add not exercised (no upload). |
| One-call script (long with `[SCENE:label]`, Shorts, SEO, scene prompts, thumbnail prompt) | Pass | run.json `write_script`: 7 keys, 27 scene prompts, tags. Markers did not leak into the transcript (0 "scene" matches); see D9. |
| Packaging A/B variants, scoring, pending file, approve/reject/list/variant CLI | Partial | 3 variants scored, winner v1 (5.5). Gate / pending file not exercised (`--no-upload` skips publish). CLI approve/reject not run. |
| AI review score + safety; `decide_privacy` | Partial | review 82/100, safety Yes. `decide_privacy` not exercised (publish skipped). |
| Edge TTS with prosody + corrections; OpenAI TTS fallback | Pass (Edge) / Not exercised (OpenAI) | Log: `Edge TTS: voice=en-IN-PrabhatNeural rate=-8% pitch=+1Hz chunks=3`. OpenAI TTS failed only because the key is invalid (401). |
| Scene images topic-aware, unique seeds, CLIP verify, thumbnail with hook text | Partial | 27 scene images + thumbnail with hook text. CLIP not exercised: torch not installed (degraded to disabled, as designed). |
| faster-whisper word timestamps, scene timeline, SRT, burned captions | Pass | 1198 words from 645.6 s; SRT 270 cues; Shorts SRT 43 cues; captions burned. Timeline fell back to proportional (D10). |
| TTS memory learning (non-fatal) | Pass | Log: `tts_memory updated (run #5)`. |
| Long 16:9 Ken Burns render + optional music; Shorts 9:16 first 6 scenes | Pass, with D8 | Long OK; no music track found, voice only (expected). Shorts uses 6 images, but see D8. |
| Upload long + Shorts, chapters, Up-next, category, pinned comment, playlist, X post | Not exercised | `--no-upload`; never run. |
| `record_video` / `--fetch-analytics` | Not exercised | No YouTube token. |
| CLI flags | Partial | `--no-upload` and default resume used. The rest are covered by unit tests only. |

## Defects found
Fixed, each with its own commit and a regression test:
1. edge-tts 7.2.8 got 403 from the Edge endpoint: floor `>=7.3.1` (plus a version guard test).
2. Module loggers bypassed `pipeline.log`: routed through `get_logger`.
3. A Claude reply with no JSON failed straight to OpenAI: one corrective retry, failure detail logged.
4. Pollinations 402 `FREE_LIMIT` (1 image / 45 s / IP) was treated as fatal: now waits the parsed "Retry after", retries up to 3 times, with proactive pacing (`POLLINATIONS_MIN_INTERVAL_S`, default 45, 0 with a key). Other 401/402/403/404 disable the provider for the run; `provider_counts` and `disabled` are stored in stage data and printed as an `Images:` summary line.
5. ImagesStage rendered a mostly empty video: it now fails (resumable) when more than 20 % of scenes are missing.
6. The thumbnail was 1024x576 when the provider returned less: now fitted to exactly 1280x720.
7. Default Pollinations model changed to `flux` (`flux-realism` 402 was probably the same rate limit; the change is harmless).

Open (need a design decision or owner action; not fixed):
- D8. Shorts: the voice mp3 is 94 s but `create_shorts_video` caps audio and background at 55 s. The caption overlays run to 94 s, so the composite is 94 s long with a black tail and captions over black (the v1 code has the same logic). Either bound the Shorts script word count so the voice fits, or truncate the composite and captions at the cap.
- D9. The review stage saw `[SCENE:...]` tags in the script and flagged them as a required fix. Confirm the reviewer should get the stripped script.
- D10. The scene timeline fell back to proportional timing even though whisper returned 1198 words (scene markers are stripped from the narration, so they cannot be matched). Not verified whether v1 behaves the same.
- Owner: `.env` has `POLLINATIONS_MODEL=flux-realism` and `IMAGE_PROVIDERS=pollinations` (no placeholder); `OPENAI_API_KEY` is invalid; the Pollinations free tier bakes a logo into images and caps at 1024x576 (a key fixes both and removes the 45 s pacing).
- The long render runs at about 2 fps (about 2.2 h for 10.8 min of video) on this CPU, the same MoviePy path as v1.
