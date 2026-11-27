# v2 parity run (partial; stopped at defect limit)

Run ID: 2026-11-27_095259 (unfinished; resume with `POLLINATIONS_MODEL=flux uv run python main.py --no-upload`).

| Step | Result |
|---|---|
| 1. `pytest -m live` | Pass: Claude structured call, Pollinations (2 tests; no OpenAI live test exists). |
| 2. `main.py --no-upload` | Not complete. script/packaging/review done on claude_cli; voice and images in progress when stopped. |
| 3. Video mechanical checks / §6.9 checklist | Not exercised (no videos rendered yet). |
| 4. OpenAI run | Not exercised. NOTE: OPENAI_API_KEY in .env returns 401 (invalid), so this step and the OpenAI TTS/LLM fallbacks cannot work until the owner rotates the key. |
| 5. `--fetch-analytics` | Skipped: no YouTube token; owner to run interactively. |
| CLIP verification | Not exercised: torch not installed. |

## Defects found (all fixed, own commits, with regression tests)
1. edge-tts 7.2.8 got HTTP 403 from the Edge endpoint; bumped to >=7.3.1.
2. Module loggers (`logging.getLogger`) bypassed `pipeline.log`, hiding fallbacks and warnings; routed via `get_logger`.
3. Claude replies with no JSON failed immediately to OpenAI; router now retries once and logs detail.
4. Pollinations default model `flux-realism` returns 402 on the keyless tier at 1280x720; default is now `flux`.

Owner action: `.env` still has `POLLINATIONS_MODEL=flux-realism`; change to `flux`.

## Update after fix round 1 (still incomplete)
- Added image-provider circuit breaker and "Images:" summary line (5b6520d) and an edge-tts floor test (840993b).
- Pollinations 402 is `FREE_LIMIT` (1 image / 45 s / IP on the keyless tier), a transient rate limit, so disabling the provider on 402 starves a run whose `.env` has `IMAGE_PROVIDERS=pollinations` only. Needs a design decision (wait-and-retry on FREE_LIMIT). See task-19-report.md.
- Step 3 and the full section 6.9 table: Not exercised (no valid videos). Step 4: skipped, OPENAI_API_KEY returns 401. Step 5: skipped, no YouTube token.
- Observed: long render ~1.2-2 fps (2-3 h for a 10.8 min video on this CPU); faster-whisper transcription of 645 s audio took ~3 min and worked; Edge TTS (7.3.1) worked; script/packaging/review ran on claude_cli.
