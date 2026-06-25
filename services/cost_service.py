"""
cost_service.py — yt-video

Lightweight SQLite-backed API cost tracker.

Records every paid API call (LLM chat, TTS, Whisper) and provides
per-run / per-day / all-time cost summaries.

DB location: <project-root>/output/costs.db  (shared with yt_ai_factory)

Usage
-----
    from services.cost_service import record_llm, record_tts, record_whisper, print_run_summary

    # After a chat.completions.create call:
    record_llm("yt_video", "script", model, res.usage.prompt_tokens, res.usage.completion_tokens)

    # After an OpenAI TTS call:
    record_tts("yt_video", "openai", "tts-1", total_chars)

    # After an OpenAI Whisper call:
    record_whisper("yt_video", audio_minutes)

    # At the end of a run:
    print_run_summary("yt_video")
"""

import sqlite3
import datetime
import logging
from pathlib import Path

_logger = logging.getLogger("cost_service")

# ---------------------------------------------------------------------------
# DB path — always project-root/output/costs.db regardless of CWD
# __file__ = yt_video/services/cost_service.py
#   .parent       = yt_video/services/
#   .parent.parent = yt_video/
#   .parent.parent.parent = project root
# ---------------------------------------------------------------------------
_DB_PATH = Path(__file__).parent.parent.parent / "output" / "costs.db"

# ---------------------------------------------------------------------------
# Pricing tables (USD, approximate March 2026)
# ---------------------------------------------------------------------------
# LLM: ($/1M input tokens,  $/1M output tokens)
_LLM_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":         (0.150,   0.600),
    "gpt-4o":              (2.500,  10.000),
    "gpt-4o-2024-11-20":   (2.500,  10.000),
    "gpt-4.1-mini":        (0.400,   1.600),
    "gpt-4.1":             (2.000,   8.000),
    "deepseek-chat":       (0.270,   1.100),
    "deepseek-reasoner":   (0.550,   2.190),
}

# TTS: $/1M characters
_TTS_PRICING: dict[str, float] = {
    "tts-1":       15.00,
    "tts-1-hd":    30.00,
    "elevenlabs": 300.00,   # ~$0.30/1K chars on standard plan
    "edge_tts":     0.00,   # Microsoft Edge TTS — completely free
    "kokoro":       0.00,   # local, free
}

# Whisper: $/minute of audio
_WHISPER_PRICING: dict[str, float] = {
    "whisper-1":       0.006,
    "faster_whisper":  0.000,   # local, free
}


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_costs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            TEXT    NOT NULL,
            pipeline      TEXT    NOT NULL,
            run_date      TEXT    NOT NULL,
            step          TEXT    NOT NULL,
            provider      TEXT    NOT NULL,
            model         TEXT    NOT NULL,
            tokens_in     INTEGER DEFAULT 0,
            tokens_out    INTEGER DEFAULT 0,
            characters    INTEGER DEFAULT 0,
            audio_minutes REAL    DEFAULT 0,
            cost_usd      REAL    NOT NULL,
            notes         TEXT
        )
    """)
    conn.commit()
    return conn


def _insert(
    pipeline: str,
    step: str,
    provider: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    characters: int = 0,
    audio_minutes: float = 0.0,
    cost_usd: float = 0.0,
    notes: str | None = None,
) -> None:
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO api_costs
                (ts, pipeline, run_date, step, provider, model,
                 tokens_in, tokens_out, characters, audio_minutes, cost_usd, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                pipeline,
                datetime.date.today().isoformat(),
                step,
                provider,
                model,
                tokens_in,
                tokens_out,
                characters,
                audio_minutes,
                round(cost_usd, 8),
                notes,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        _logger.warning(f"[cost] DB write failed (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Public recording helpers
# ---------------------------------------------------------------------------
def record_llm(
    pipeline: str,
    step: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    notes: str | None = None,
) -> float:
    """Record an LLM chat completion call.  Returns estimated cost in USD."""
    key = model.lower()
    pricing = _LLM_PRICING.get(key)
    if pricing is None:
        # Try prefix match for model variants (e.g. "gpt-4o-mini-2024-07-18")
        for k, v in _LLM_PRICING.items():
            if key.startswith(k):
                pricing = v
                break
    if pricing is None:
        pricing = _LLM_PRICING["gpt-4o-mini"]   # conservative fallback

    cost = (tokens_in * pricing[0] + tokens_out * pricing[1]) / 1_000_000
    provider = "deepseek" if "deepseek" in key else "openai"
    _insert(pipeline, step, provider, model, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost, notes=notes)
    _logger.info(f"[cost] {step}/{model}: {tokens_in:,}in + {tokens_out:,}out = ${cost:.6f}")
    return cost


def record_tts(
    pipeline: str,
    provider: str,
    model: str,
    characters: int,
    notes: str | None = None,
) -> float:
    """Record a TTS call.  Returns estimated cost in USD."""
    rate = _TTS_PRICING.get(model.lower(), _TTS_PRICING.get(provider.lower(), 0.0))
    cost = characters * rate / 1_000_000
    _insert(pipeline, "tts", provider, model, characters=characters, cost_usd=cost, notes=notes)
    if rate > 0:
        _logger.info(f"[cost] tts/{model}: {characters:,} chars = ${cost:.6f}")
    return cost


def record_whisper(
    pipeline: str,
    audio_minutes: float,
    model: str = "whisper-1",
    notes: str | None = None,
) -> float:
    """Record an OpenAI Whisper API transcription call.  Returns estimated cost in USD."""
    rate = _WHISPER_PRICING.get(model.lower(), 0.006)
    cost = audio_minutes * rate
    _insert(pipeline, "transcribe", "openai", model,
            audio_minutes=audio_minutes, cost_usd=cost, notes=notes)
    _logger.info(f"[cost] whisper/{model}: {audio_minutes:.2f} min = ${cost:.6f}")
    return cost


# ---------------------------------------------------------------------------
# Summary / reporting
# ---------------------------------------------------------------------------
def get_run_summary(pipeline: str, run_date: str | None = None) -> dict:
    """Return cost breakdown for today (or a specific date)."""
    date = run_date or datetime.date.today().isoformat()
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT step, provider, model, tokens_in, tokens_out, characters,
                   audio_minutes, cost_usd
            FROM api_costs
            WHERE pipeline = ? AND run_date = ?
            ORDER BY id
            """,
            (pipeline, date),
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    total = sum(r[7] for r in rows)
    return {"date": date, "pipeline": pipeline, "total_usd": round(total, 6), "rows": rows}


def print_run_summary(pipeline: str, run_date: str | None = None) -> None:
    """Log a human-readable cost summary for the current run to the pipeline logger."""
    try:
        info = get_run_summary(pipeline, run_date)
        if not info["rows"]:
            _logger.info(f"[cost] No paid API calls recorded for {pipeline} on {info['date']}")
            return

        _logger.info(f"[cost] ━━━ API Cost Summary — {pipeline} — {info['date']} ━━━")
        header = f"{'Step':<16} {'Provider':<12} {'Model':<22} {'TokIn':>8} {'TokOut':>8} {'Chars':>8} {'Min':>6}  {'Cost $':>10}"
        _logger.info("[cost] " + header)
        _logger.info("[cost] " + "─" * len(header))
        for step, provider, model, ti, to, ch, am, cost in info["rows"]:
            line = (
                f"{step:<16} {provider:<12} {model:<22}"
                f" {ti:>8,} {to:>8,} {ch:>8,} {am:>6.2f}  ${cost:>9.6f}"
            )
            _logger.info("[cost] " + line)
        _logger.info("[cost] " + "─" * len(header))
        _logger.info(f"[cost] Total estimated cost: ${info['total_usd']:.4f} USD")
    except Exception as exc:
        _logger.warning(f"[cost] Could not generate cost summary: {exc}")


def get_alltime_summary() -> dict:
    """Return aggregate costs grouped by pipeline and date."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT pipeline, run_date, SUM(cost_usd) as total
            FROM api_costs
            GROUP BY pipeline, run_date
            ORDER BY run_date DESC, pipeline
            """
        ).fetchall()
        conn.close()
        return {"rows": rows}
    except Exception:
        return {"rows": []}
