# yt-video v2 — Redesign

- **Date:** 2026-11-26
- **Branch:** `feat/redesign-v2` (cut from `master` after PR #3 merged at `4bab4a7`)
- **Status:** Reviewed (builder check done), awaiting owner approval

---

## 1. Intent

yt-video automates a faceless YouTube channel. It finds a trending topic, writes the script, generates the voice, images and video (long-form plus a Short), uploads both, and learns from analytics.

The owner wants two things:

1. **Run text generation through the Claude Code CLI** (`claude -p`) on their existing Claude plan, so scripts and the other model calls use Claude first. If Claude can't be used for a call, fall back to the **latest cheap OpenAI GPT** model.
2. **A full structural redesign** that leaves the project easier to change and test. Today it is one 530-line orchestration function plus 17 service modules (`services/`, excluding `__init__.py`). Each module builds its own API client, and the modules share global state.

Git history is kept: no rewrites, and the work lands as normal commits on a feature branch.

## 2. Goals, non-goals, anti-goals

**Goals**

- Every LLM call goes through one provider layer: Claude CLI first, OpenAI fallback, chosen per call.
- Image generation goes through one provider chain with free providers first.
- The pipeline is a sequence of small, independently testable stages with explicit inputs and outputs.
- **Feature parity** with Phases A–C (see §6.9).
- All durable learned state (TTS memory, analytics memory, playlists, YouTube token, cost DB) is kept and migrated, not reset.
- Unit tests run offline with fake providers, with no network and no Claude/OpenAI calls.
- Every run reports which provider served each call, plus the estimated USD cost.

**Non-goals**

- GitHub Actions, CI or any cloud hosting. The pipeline runs locally only.
- Local image models. The machine has Intel UHD graphics and no CUDA GPU, so FLUX/SD on CPU is impractically slow.
- Changing the voice tech (Edge TTS) or transcription tech (faster-whisper / Whisper).
- New content features. This is a restructure plus a provider swap.

**Anti-goals** (failure even if it "works")

- Any run silently costs real money when Claude was available. A fallback must be logged and visible in the run summary.
- The Claude CLI subprocess picks up the owner's personal Claude Code setup (plugins, hooks, CLAUDE.md, MCP servers). That would pollute prompts, inflate token use and make output non-deterministic.
- The analytics and TTS feedback loops lose their accumulated memory.
- An unreviewed video goes **public** because of a bug in the new gates. Privacy defaults to `private` on any doubt.

## 3. Constraints

- **Platform:** Windows 11, PowerShell/Git Bash, Python managed with `uv` (`uv run ...`). 20 GB RAM, no NVIDIA GPU.
- **Claude Code CLI:** v2.1.332 installed at `~/.local/bin/claude.exe`, authenticated with the owner's Claude subscription (OAuth).
  - `--bare` must **not** be used. It restricts auth to `ANTHROPIC_API_KEY`/apiKeyHelper and skips OAuth/keychain, which would break subscription use.
  - Relevant flags verified in `claude --help`: `-p/--print`, `--output-format json`, `--json-schema <schema>`, `--model <alias>`, `--fallback-model`, `--system-prompt`, `--tools ""`, `--no-session-persistence`, `--strict-mcp-config`, `--setting-sources`, `--max-budget-usd`.
- **Claude usage limits:** Claude CLI calls count against the same plan limits as the owner's interactive Claude Code use. A usage-limit response is expected sometimes and is a normal fallback trigger, not a crash.
- **OpenAI fallback model:** `gpt-6-luna` (official pricing page, fetched 2026-11-25: $0.10 in / $0.50 out per 1M tokens; cheapest current-generation GPT). `gpt-5-nano` is cheaper ($0.05/$0.40), but it is a generation older and its deprecation status is unverified. The model ID is config, not code.
- **Images:**
  - Pollinations keyless GET `https://image.pollinations.ai/prompt/...` returned `200 image/jpeg` on 2026-11-25.
  - Cloudflare Workers AI free tier reportedly gives ~10k neurons/day (~170 FLUX.1 schnell images). This is unverified and needs a free account.
- **Existing PRs and branches:** Phases A–C are on `master`. Nothing else is in flight.

## 4. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| A | Add a shared LLM/image provider layer and repoint the 5 LLM call sites; leave the rest as is | Lowest risk and smallest diff, but keeps the monolithic orchestrator and global state |
| **B** | **A plus a full restructure into a package with stages, providers, typed models and a tests folder** | Touches every file, but the result is maintainable and testable. **Chosen by the owner.** |
| C | Make Claude Code the orchestrator; Python modules become tools it calls | Most flexible, but non-deterministic, hard to test, slow, and burns plan limits fastest. Rejected. |

**Picked: B.** The owner wants a "full redesign" and judged it worth the larger change. PR #3 was merged first so the redesign starts from all of Phases A–C with no branch conflicts.

## 5. Decisions

### 5.1 Made by the owner

1. Full redesign (approach B), on a feature branch with history kept.
2. Claude Code CLI is the first choice for all text generation.
3. If Claude doesn't work for a call, fall back to the latest cheapest GPT (`gpt-6-luna`).
4. Local runs and local tests only, with no GitHub Actions.
5. PR #3 merged into `master` before starting (done: `4bab4a7`).
6. **If the spike shows the CLI can't be fully isolated** from the owner's plugins, hooks and memory, accept the overhead and keep Claude first. The extra context costs plan quota, not money. The run summary reports the per-call overhead. The anti-goal in §2 is relaxed to "minimized and visible".
7. **Extra usage (pay-as-you-go overage) is ON** for the owner's Claude plan. So the router stops using Claude for the rest of the run on the **first** usage-limit or overage signal, not after 2.

### 5.2 Made by Claude (owner can override)

1. **Model per task.**
   - Claude `sonnet` for the script and scene-prompt refinement.
   - Claude `haiku` for topic ranking, review, packaging variants and the content-angle fallback.
   - OpenAI fallback uses `gpt-6-luna` for all tasks.
   - The mapping lives in config.
2. **Structured output everywhere.** Each LLM task defines a pydantic model. Its JSON Schema is passed to Claude via `--json-schema` and to OpenAI via structured outputs. This replaces today's `[SEO_TITLE]`-style text-marker parsing. It adds a `pydantic` dependency.
3. **Image chain:** Pollinations → Cloudflare Workers AI (only if credentials are configured) → existing PIL gradient placeholder. CLIP verification stays and applies to whichever provider served the image.
4. **Per-run artifact folders and a single durable state folder** (§6.6).
5. **Typer is not added.** The CLI stays `argparse` with the same flags, so existing habits keep working.
6. **Fallback is automatic for every task, including the script.** This follows the owner's instruction ("if not [working] use gpt"). It is logged, shown in the run summary and recorded in the cost ledger. `--claude-only` opts out.
7. **The spike may change the model mapping.** If the spike finds that aliases don't work with `--model`, the plan switches to full model IDs. If `haiku` can't reliably fill a schema, that task moves to `sonnet`. Any change is recorded in the plan.
8. **Output quality bars** are enforced by schema validators. They mirror today's prompt targets. A failure counts as a schema failure: retry once, then fall back.
   - Long script: 1,200–1,900 words, 24–36 `[SCENE:label]` markers. Markers stay inline in the `long_script` string field.
   - Shorts script: 110–180 words.
   - SEO title: ≤ 70 chars. Description: 400–480 chars. Tags: exactly 10.
   - *Amended 2026-11-27 after a real run failed on near misses (1,080 and 1,191 words, 183 Shorts words, 342-char description):* the numbers above stay as the **prompt targets**. Validators accept a wider band and reject only broken output: long 1,000–2,200 words with 20–40 markers, Shorts 90–220 words, title ≤ 100 chars (YouTube's limit), description 250–700 chars, and 8–15 tags (trimmed to 10).
9. **Cloudflare provider:** built and contract-tested against fixtures written from Cloudflare's API docs. It is skipped unless credentials are set, and its live test runs only when they are.

## 6. Design

### 6.1 Package layout

```
yt-video/
├── pyproject.toml            # entry point: `yt-video = yt_video.cli:main`
├── main.py                   # thin shim → yt_video.cli.main (keeps `uv run python main.py`)
├── yt_video/
│   ├── cli.py                # argparse; same flags as today
│   ├── settings.py           # one Settings object from .env (pydantic), no import-time side effects
│   ├── logging.py
│   ├── models.py             # Topic, ScriptOutput, Review, PackagingVariant, SceneImage, RunResult ...
│   ├── pipeline/
│   │   ├── context.py        # RunContext: settings, providers, run dir, state store
│   │   ├── runner.py         # ordered stage execution + checkpoint/resume
│   │   └── stages/           # one module per stage (§6.2)
│   ├── providers/
│   │   ├── llm/              # base.py, claude_cli.py, openai_api.py, router.py, fake.py
│   │   ├── image/            # base.py, pollinations.py, cloudflare.py, placeholder.py, chain.py, fake.py
│   │   ├── tts/              # edge.py (+ openai fallback kept as today)
│   │   └── transcribe/       # faster_whisper.py (+ openai whisper fallback)
│   ├── domain/               # pure logic: formats, niche filter, rerank, packaging scoring,
│   │                         #   privacy decision, chapters, slug, scene-marker parsing
│   ├── memory/               # tts_memory.py, analytics_memory.py
│   ├── integrations/         # youtube.py, trends.py, reddit.py, rss.py, x.py
│   ├── media/                # video render, shorts render, captions, thumbnail, clip verify
│   ├── prompts/              # prompt templates as .md/.txt files, loaded by name
│   └── costs.py              # sqlite cost ledger
├── tests/
│   ├── unit/                 # domain + stages with fakes (offline)
│   ├── providers/            # provider contract tests with recorded responses (offline)
│   └── live/                 # opt-in real-call smoke tests (`-m live`), never run by default
├── docs/
└── state/  output/           # runtime, gitignored
```

Today's `services/` and `workflows/` are deleted at the end, once parity is verified (§7).

### 6.2 Stages

Each stage is a class with `name`, `run(ctx, inputs) -> outputs` and typed input/output models. The runner persists each stage's output, so a re-run resumes at the first unfinished stage. This keeps today's checkpoint behavior.

| # | Stage | Replaces (today) |
|---|---|---|
| 1 | `discover` (fetch signals) | `fetch_trending_topics` |
| 2 | `rank_topics` (LLM rank + rerank + niche lock) | `filter_and_score_topics`, `rerank_topics`, `apply_niche_hard_filter` |
| 3 | `pick_format` | `pick_format` |
| 4 | `write_script` (LLM, long + shorts + SEO + scene prompts) | `generate_script` |
| 5 | `packaging` (LLM variants + scoring + pending-approval file) | `generate_packaging_variants`, `pick_best_packaging`, `write_packaging_pending` |
| 6 | `review` (LLM score + safety) | `review_script` |
| 7 | `voice` (long + shorts) | `generate_voice` ×2 |
| 8 | `images` (LLM refine prompts → image chain → CLIP verify → thumbnail) | `refine_scene_prompts`, `generate_scene_images_for_scenes`, `generate_thumbnail` |
| 9 | `transcribe` (long + shorts captions, scene timeline) | `transcribe_and_sync`, `transcribe_words` |
| 10 | `learn_tts` (non-fatal) | `analyze_and_update` |
| 11 | `render` (long 16:9 + Shorts 9:16) | `create_video`, `create_shorts_video` |
| 12 | `publish` (privacy decision, packaging gate, upload long + shorts, comment, playlist, X post, analytics record) | Phase 5 block |

**Gate behavior must stay the same.** When packaging approval is required and the video would go public, `publish` stops the run with a "waiting for approval" status. It does not error out. `--approve-packaging` / `--reject-packaging` then let the next run continue. If the approved title or hook changed, the thumbnail is regenerated.

### 6.3 LLM provider layer

```python
class LLMRequest(BaseModel):
    task: str                      # "script", "rank_topics", "review", ...
    system: str
    prompt: str
    schema: type[BaseModel] | None # structured output when set
    max_output_tokens: int
    timeout_s: int

class LLMResult(BaseModel):
    provider: str                  # "claude_cli" | "openai"
    model: str
    text: str
    data: dict | None              # validated against schema
    usage: Usage | None
    cost_usd: float                # 0.0 for claude_cli (plan), estimate for openai
    fallback_reason: str | None
```

`LLMRouter.complete(req)` tries providers in the configured order (default `claude_cli,openai`).

**Claude CLI provider**

- Invocation:
  ```
  claude -p --model <alias> --output-format json [--json-schema <schema>]
         --system-prompt <task system prompt> --tools ""
         --no-session-persistence --strict-mcp-config
  ```
- The prompt goes on **stdin**, not argv, because Windows caps command lines at ~32k chars and scripts can be long.
- Runs with `cwd` set to an empty, per-run scratch dir, so no project CLAUDE.md is discovered.
- **Isolation from the owner's personal setup must be verified in the first spike** (§7, step 1). The spike measures whether user plugins, hooks or memory still inject context under `--setting-sources`/`--system-prompt`, and picks the flag set with the lowest overhead.
- Pass bar: the response contains no plugin- or hook-injected text, and the per-call input tokens are reported.
- If overhead remains, accept it (owner decision §5.1.6) and show it in the run summary. This is not a blocker.
- Parses the JSON envelope (`result`, error flag, usage fields). Exact field names are confirmed in the spike.

**Fallback triggers.** The router moves to the next provider and records the reason when:

1. `claude` is not on PATH.
2. It exits non-zero.
3. It times out.
4. The envelope reports an error or a usage/rate limit.
5. The output fails schema validation after **one** retry with the validation error appended.

**OpenAI provider**

- Official `openai` SDK.
- Model from config (default `gpt-6-luna`).
- Structured outputs when `schema` is set.
- Cost recorded via the cost ledger using the pricing table in config.

**Circuit breaker.** The router skips Claude for the rest of the run on the **first** usage-limit, rate-limit or overage-warning signal from the CLI. The owner has extra usage enabled, so continuing past a limit could bill real money.

- The spike records exactly what those signals look like (exit code, envelope fields, stderr text) and the parser matches them.
- Claude's envelope cost field (e.g. `total_cost_usd`) is recorded in the ledger as **notional**. Plan and overage usage can't be told apart from it.
- `--max-budget-usd` is not passed.

After a timeout or a non-zero exit (anything other than a usage limit), Claude is still tried for the next call.

**No silent spend.** `--claude-only` turns fallback off for a run and fails instead. The run summary always lists the provider used for each call.

**Fakes.** `FakeLLM` returns canned, schema-valid responses keyed by `task`. All stage tests use it.

### 6.4 Image provider layer

- `ImageProvider.generate(prompt, width, height, seed) -> bytes | None`
- `ImageChain` tries providers in order (default `pollinations,cloudflare,placeholder`). Providers without credentials are skipped automatically.
- The content-addressed cache stays, keyed by provider + model + prompt + size + seed.
- CLIP verify with regenerate-on-low-score stays, with the same `CLIP_VERIFY` / `CLIP_MIN_SCORE` config.
- OpenAI `gpt-image-*` stays available as an opt-in provider (paid) but is never in the default chain.

### 6.5 Settings

- A single pydantic `Settings` loaded once from `.env` and passed through `RunContext`. Modules do not read `os.getenv` directly.
- Existing env var names are kept.
- New variables:

| Variable | Default |
|---|---|
| `LLM_PROVIDERS` | `claude_cli,openai` |
| `CLAUDE_CLI_PATH` | `claude` |
| `CLAUDE_MODEL_SCRIPT` | `sonnet` |
| `CLAUDE_MODEL_LIGHT` | `haiku` |
| `CLAUDE_TIMEOUT_S` | `600` for script, `180` for others |
| `OPENAI_FALLBACK_MODEL` | `gpt-6-luna` |
| `IMAGE_PROVIDERS` | `pollinations,cloudflare,placeholder` |
| `CLOUDFLARE_ACCOUNT_ID` | none |
| `CLOUDFLARE_API_TOKEN` | none |

- `OPENAI_API_KEY` becomes **optional**. It is needed only for fallback and optional paid providers.
- `SCRIPT_MODEL`/`DEEPSEEK_*` are retired. DeepSeek support is dropped, since the owner's fallback choice is GPT. Recorded as a decision; the owner can veto.

### 6.6 Files and state

**Durable state** lives in `state/` (gitignored, never auto-deleted):

| File | Moved from |
|---|---|
| `state/tts_style_memory.json` | `output/` |
| `state/yt_analytics_memory.json` | already there |
| `state/playlists.json` | already there |
| `state/youtube_token.json` | already there |
| `state/costs.db` | today `<repo>/../output/costs.db`, **outside the repo**, a legacy path bug from `yt_ai_factory` |
| `state/review_log.json`, `state/analytics_log.json`, `state/x_post_log.json` | `output/`; `x_post_log.json` also had the outside-repo bug |
| `state/packaging_pending.json` | `output/` |

**Per-run artifacts** go in `output/runs/<YYYY-MM-DD>_<slug>/`: voice, images, transcripts, SRTs, timeline, mp4s, script and log.

- `--restart` deletes only the current run's folder. This fixes today's glob cleanup, which misses Shorts mp4s and SRTs.
- Checkpoint state is stored per run folder (`run.json`) and keyed by a run ID instead of the date, so more than one run per day is possible. By default `main.py` still resumes the latest unfinished run.

**One-time migration**

- `yt-video migrate-state` copies (does not move) legacy files into `state/`.
- It is idempotent and runs automatically on first v2 start if `state/` lacks the files.
- It never deletes legacy files; the owner can clean those up after checking.
- A file is copied only when the target is absent. Nothing is merged. This includes `costs.db`, which is looked up at both `<repo>/output/costs.db` and the legacy `<repo>/../output/costs.db`.
- A missing source is skipped silently. A source that fails to parse is skipped with a warning naming it. Migration never aborts the run.
- **v1 checkpoints are not migrated.** The last v1 run (2026-08-02, "Bumrah", rendered but not uploaded) stays a v1 run. If the owner wants it uploaded, that happens with v1 before cutover.

**Runs and approvals**

- At most one unfinished run at a time. `main.py` resumes it. Starting a fresh run while one is unfinished needs `--restart` (discard it) or `--new` (abandon it, keep its folder).
- The packaging approval lives in the run folder (`output/runs/<id>/packaging_pending.json`) and carries the run ID. `state/` holds only a pointer to the run awaiting approval.
- `--approve-packaging` / `--reject-packaging` / `--list-packaging` act on that run only. If no run is awaiting approval, they error out.
- Approval data for one run can never apply to another.
- Run folders are never auto-pruned. Analytics (`record_video`, `--fetch-analytics`) depends only on `state/` and the YouTube API, never on run-folder files.

### 6.7 Prompts

- Prompt text moves out of Python strings into `yt_video/prompts/*.md` templates.
- The content is kept from today's prompts, including the Phase A humanize rules, format blocks, niche-lock note, TTS-memory notes and analytics notes.
- Wording changes only where needed to request JSON matching the schema instead of text markers.
- The feedback-loop injections stay. They are rendered into the templates the same way as today.

### 6.8 Cost ledger and run summary

- The SQLite ledger records every call: provider, model, task, tokens, USD.
- Claude CLI calls are recorded at $0 with token counts when the envelope provides them, so plan usage is visible.
- The end-of-run summary prints, per stage, the provider used, any fallback reasons and the total USD.

### 6.9 Feature parity checklist

The redesign must preserve all of these:

- Trend sources: pytrends, Reddit, RSS, X trends scrape, yfinance news
- LLM topic ranking, novelty/velocity/niche rerank, hard niche lock, recent-topic dedupe
- Format library pick, plus format-to-playlist mapping
- One-call script generation: long script with `[SCENE:label]`, Shorts script, SEO title/description/tags, scene prompts, thumbnail prompt
- Packaging A/B variants, scoring, pending file, approve/reject/list/variant CLI
- AI review score + safety; `decide_privacy` (≥80 and safe → public, else private; missing safety → "No")
- Edge TTS with prosody and learned pronunciation corrections; OpenAI TTS fallback
- Scene images with topic-aware prompts, unique seeds, CLIP verify/regenerate, thumbnail with hook text
- faster-whisper word timestamps, scene timeline, SRT, burned captions (long + Shorts)
- TTS memory learning (non-fatal)
- Long 16:9 render with Ken Burns and optional background music; Shorts 9:16 with the first 6 scenes
- Upload long + Shorts, description chapters, Up-next blurb, category mapping, pinned comment, playlist add (public long only), X post
- `record_video` for the 48h analytics fetch; `--fetch-analytics` updates memory (retention, CTR, traffic sources, subs)
- CLI: `--restart`, `--list-voices`, `--fetch-analytics`, `--no-upload`, `--approve-packaging`, `--reject-packaging`, `--variant`, `--list-packaging`

**Dead code to remove:**

- `fetch_nifty_movers`, a leftover from `yt_ai_factory`
- Unused `ticker_items`/`stat_cards` render args, unless the builder finds a live use

## 7. Delivery

The work ships as a series of commits on `feat/redesign-v2`. The old code keeps working until the end, so there is always a runnable pipeline. The root-level v1 tests keep passing until step 8, when they are deleted along with `services/` and `workflows/`. Their cases are ported into `tests/` in step 4.

1. **Spike: Claude CLI.** Measure isolation (§6.3), JSON envelope fields, `--json-schema` behavior, latency for a script-sized prompt, and what a usage-limit error looks like. Throwaway; findings recorded in the plan.
2. Package skeleton: settings, models, logging, cost ledger, state paths and migration.
3. Provider layers: LLM router (Claude CLI + OpenAI + fake), image chain, TTS and transcribe wrappers, with contract tests.
4. Domain logic ported as pure functions, with tests moved and extended from today's 5 test files.
5. Stages + runner, each stage tested with fakes.
6. CLI wiring and a `main.py` shim.
7. **Parity run:** a real end-to-end `--no-upload` run on v2, then a check of the outputs against the §6.9 checklist.
8. Delete `services/` and `workflows/`, update README and env.example.

## 8. Testing

- `uv run pytest` runs offline by default and uses fakes for every provider.
- Contract tests feed recorded Claude envelopes and OpenAI responses (success, error, usage-limit, invalid JSON) through the real provider parsers.
- Router tests cover each fallback trigger, the retry-once rule, the circuit breaker and `--claude-only`.
- `pytest -m live` runs opt-in real smoke tests: one Claude CLI call, one OpenAI call, one Pollinations image. They are never part of the default run.
- Acceptance for the whole redesign:
  - §7 step 7 produces long + Shorts mp4s, SRTs, thumbnail and run summary.
  - The summary shows Claude served the LLM calls.
  - A forced-fallback run (`LLM_PROVIDERS=openai`) also completes.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Claude CLI subprocess inherits the owner's plugins/hooks/memory | Spike measures it first; neutral cwd, `--system-prompt`, `--tools ""`, `--strict-mcp-config`, `--setting-sources` |
| Plan usage limits hit mid-run, or interactive work eats quota; extra usage is on, so overage would bill | Circuit breaker trips on the **first** limit/overage signal → OpenAI for the rest of the run; run summary shows it |
| CLI flag or envelope format changes in a Claude Code update | Parsing isolated in one module, covered by contract tests; `claude --version` logged per run |
| Pollinations goes key-only or rate-limited | Cloudflare next in chain; `POLLINATIONS_KEY` already supported |
| `gpt-6-luna` renamed or retired | Model ID is config; pricing table in config |
| Big-bang redesign breaks a subtle Phase A–C behavior | Parity checklist (§6.9) + parity run before deleting old code |
| State migration loses learned memory | Copy-not-move, idempotent, legacy files kept |

## 10. Left to the builder

- Internal class and function names, module boundaries within each folder, and the exact shape of `RunContext`.
- Whether stages share one module per stage or group small ones.
- Exact prompt-template mechanism (format strings vs. a tiny template helper; no Jinja unless needed).
- Retry/backoff details for image providers and trend scrapers.
- Logging format, as long as provider/fallback/cost appear in the run summary.
