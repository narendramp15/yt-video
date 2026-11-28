"""SQLite cost ledger: records every LLM/TTS/transcribe/image call and summarises a run."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

Kind = Literal["llm", "tts", "transcribe", "image"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL NOT NULL,
    notional_cost_usd REAL,
    fallback_reason TEXT
)
"""
_COLUMNS = (
    "ts", "run_id", "stage", "kind", "provider", "model", "input_tokens",
    "output_tokens", "cost_usd", "notional_cost_usd", "fallback_reason",
)


def estimate_openai_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, tuple[float, float]],
) -> float:
    """Cost in USD; prices are per 1M tokens. Unknown model raises KeyError."""
    in_price, out_price = pricing[model]
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


class CostLedger:
    """Append-only call ledger. Short-lived connection per call (Windows file locking)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(_SCHEMA)
        return conn

    def record(
        self,
        run_id: str,
        stage: str,
        kind: Kind,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float,
        notional_cost_usd: float | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn:
            conn.execute(
                f"INSERT INTO calls ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                (ts, run_id, stage, kind, provider, model, input_tokens, output_tokens,
                 cost_usd, notional_cost_usd, fallback_reason),
            )
            conn.commit()

    def run_rows(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM calls WHERE run_id = ? ORDER BY id", (run_id,))
            return [dict(r) for r in cur.fetchall()]

    def run_total_usd(self, run_id: str) -> float:
        with closing(self._connect()) as conn:
            (total,) = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM calls WHERE run_id = ?", (run_id,)
            ).fetchone()
        return float(total)


def format_run_summary(rows: list[dict]) -> str:
    """One line per call, a TOTAL line (real cost only) and the Claude overhead totals."""
    lines = []
    for r in rows:
        line = (
            f"{r['stage']:<20} {r['provider']:<14} {r['model']:<16} ${r['cost_usd']:.6f}"
        )
        if r.get("fallback_reason"):
            line += f"  (fallback: {r['fallback_reason']})"
        lines.append(line)
    total = sum(r["cost_usd"] for r in rows)
    claude = [r for r in rows if r["provider"] == "claude_cli"]
    claude_in = sum(r.get("input_tokens") or 0 for r in claude)
    claude_notional = sum(r.get("notional_cost_usd") or 0.0 for r in claude)
    lines.append(f"TOTAL ${total:.6f}")
    lines.append(f"Claude input tokens: {claude_in}")
    lines.append(f"Claude notional cost: ${claude_notional:.4f}")
    return "\n".join(lines)
