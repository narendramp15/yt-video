import sqlite3

import pytest

from yt_video.costs import CostLedger, estimate_openai_cost, format_run_summary

PRICING = {"gpt-6-luna": (0.10, 0.50)}


def test_estimate_luna():
    assert estimate_openai_cost("gpt-6-luna", 1_000_000, 1_000_000, PRICING) == pytest.approx(0.60)


def test_estimate_unknown_model_raises():
    with pytest.raises(KeyError):
        estimate_openai_cost("nope", 1, 1, PRICING)


def test_record_and_total(tmp_path):
    led = CostLedger(tmp_path / "costs.db")
    led.record("r1", "write_script", "llm", "claude_cli", "sonnet", 100, 200, 0.0, notional_cost_usd=0.03)
    led.record("r1", "review", "llm", "openai", "gpt-6-luna", 10, 10, 0.000006, fallback_reason="usage_limit")
    assert led.run_total_usd("r1") == pytest.approx(0.000006)
    s = format_run_summary(led.run_rows("r1"))
    assert "usage_limit" in s and "claude_cli" in s and "TOTAL" in s


def test_creates_parent_dirs_and_isolates_runs(tmp_path):
    db = tmp_path / "a" / "b" / "costs.db"
    led = CostLedger(db)
    led.record("r1", "s", "tts", "edge", "voice", None, None, 0.0)
    led.record("r2", "s", "image", "pollinations", "m", None, None, 0.01)
    assert db.exists()
    rows = led.run_rows("r1")
    assert len(rows) == 1 and rows[0]["kind"] == "tts" and rows[0]["input_tokens"] is None
    assert rows[0]["ts"]
    assert led.run_total_usd("r1") == 0.0
    assert led.run_total_usd("missing") == 0.0
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from calls").fetchone()[0] == 2


def test_summary_claude_overhead():
    rows = [
        {"stage": "a", "provider": "claude_cli", "model": "sonnet", "input_tokens": 100,
         "cost_usd": 0.0, "notional_cost_usd": 0.03, "fallback_reason": None},
        {"stage": "b", "provider": "claude_cli", "model": "haiku", "input_tokens": 50,
         "cost_usd": 0.0, "notional_cost_usd": 0.01, "fallback_reason": None},
    ]
    s = format_run_summary(rows)
    assert "Claude input tokens: 150" in s
    assert "$0.0400" in s
    assert "TOTAL" in s
