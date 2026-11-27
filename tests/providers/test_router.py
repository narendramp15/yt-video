from __future__ import annotations

import pytest
from pydantic import BaseModel

from yt_video.costs import CostLedger
from yt_video.providers.llm.base import LLMError, LLMRequest, ProviderError, RawCompletion
from yt_video.providers.llm.fake import FakeLLM
from yt_video.providers.llm.router import LLMRouter, build_router
from yt_video.settings import Settings


class Out(BaseModel):
    title: str


def req(schema=None, task="t") -> LLMRequest:
    return LLMRequest(task=task, system="s", prompt="p", schema=schema, tier="script")


def pair(claude_resp, openai_resp=None):
    claude = FakeLLM({"t": claude_resp}, name="claude_cli")
    openai = FakeLLM({"t": openai_resp if openai_resp is not None else {"title": "o"}}, name="openai")
    return claude, openai


def test_claude_success_no_fallback():
    claude, openai = pair({"title": "c"})
    result = LLMRouter([claude, openai], None, "r").complete(req(Out), "script")
    assert result.provider == "claude_cli" and result.fallback_reason is None
    assert result.parsed == Out(title="c")


def test_usage_limit_disables_claude_for_run():
    claude, openai = pair(ProviderError("usage_limit"))
    router = LLMRouter([claude, openai], None, "r")
    r1 = router.complete(req(), "s")
    r2 = router.complete(req(), "s")
    assert r1.provider == r2.provider == "openai"
    assert len(claude.requests) == 1
    assert router.disabled == {"claude_cli"}


def test_timeout_falls_back_but_claude_retried_next_call():
    claude, openai = pair([ProviderError("timeout"), {"title": "c"}])
    router = LLMRouter([claude, openai], None, "r")
    assert router.complete(req(), "s").provider == "openai"
    assert router.complete(req(), "s").provider == "claude_cli"
    assert router.disabled == set()


def test_invalid_then_valid_retries_same_provider():
    claude, openai = pair([{"wrong": 1}, {"title": "c"}])
    result = LLMRouter([claude, openai], None, "r").complete(req(Out), "s")
    assert result.provider == "claude_cli"
    assert len(claude.requests) == 2
    assert "failed validation" in claude.requests[1].prompt
    assert claude.requests[1].prompt.endswith("Return corrected JSON only.")
    assert openai.requests == []


def test_no_json_provider_error_retries_once_then_succeeds():
    claude, openai = pair([ProviderError("invalid_output", "no JSON in claude output"), {"title": "c"}])
    result = LLMRouter([claude, openai], None, "r").complete(req(Out), "s")
    assert result.provider == "claude_cli"
    assert len(claude.requests) == 2
    assert "no JSON in claude output" in claude.requests[1].prompt
    assert openai.requests == []


def test_invalid_twice_falls_back():
    claude, openai = pair([{"wrong": 1}, {"wrong": 2}])
    result = LLMRouter([claude, openai], None, "r").complete(req(Out), "s")
    assert result.provider == "openai"
    assert "invalid_output" in result.fallback_reason
    assert result.fallback_reason.startswith("claude_cli: invalid_output")


def test_claude_only_never_calls_openai():
    claude, openai = pair(ProviderError("exit"))
    with pytest.raises(LLMError):
        LLMRouter([claude, openai], None, "r", claude_only=True).complete(req(), "s")
    assert openai.requests == []


def test_all_fail_lists_reasons():
    class Down(FakeLLM):
        def available(self) -> bool:
            return False

    claude = Down({}, name="claude_cli")
    openai = FakeLLM({"t": ProviderError("api")}, name="openai")
    with pytest.raises(LLMError) as ei:
        LLMRouter([claude, openai], None, "r").complete(req(), "s")
    assert "claude_cli: unavailable" in str(ei.value)
    assert "openai: api" in str(ei.value)


def test_ledger_records_fallback_reason(tmp_path):
    ledger = CostLedger(tmp_path / "c.db")
    claude, openai = pair(ProviderError("usage_limit"))
    LLMRouter([claude, openai], ledger, "run1").complete(req(), "script")
    rows = ledger.run_rows("run1")
    assert len(rows) == 1
    assert rows[0]["fallback_reason"] == "claude_cli: usage_limit"
    assert rows[0]["provider"] == "openai" and rows[0]["stage"] == "script"


def test_failed_paid_call_is_recorded(tmp_path):
    ledger = CostLedger(tmp_path / "c.db")
    err = ProviderError("usage_limit", cost_usd=0.42, input_tokens=10, output_tokens=5, model="sonnet")
    claude, openai = pair(err)
    LLMRouter([claude, openai], ledger, "run1").complete(req(), "script")
    rows = ledger.run_rows("run1")
    failed = [r for r in rows if r["provider"] == "claude_cli"]
    assert len(failed) == 1
    assert failed[0]["cost_usd"] == 0.42 and failed[0]["model"] == "sonnet"
    assert failed[0]["fallback_reason"] == "usage_limit"
    assert len(rows) == 2


def test_failed_paid_call_unknown_model(tmp_path):
    ledger = CostLedger(tmp_path / "c.db")
    claude, openai = pair(ProviderError("api", cost_usd=0.1))
    LLMRouter([claude, openai], ledger, "r").complete(req(), "s")
    assert ledger.run_rows("r")[0]["model"] == "unknown"


def test_invalid_first_paid_completion_is_recorded(tmp_path):
    class Paid(FakeLLM):
        def complete(self, r):
            c = super().complete(r)
            c.cost_usd = 0.3
            return c

    ledger = CostLedger(tmp_path / "c.db")
    claude = Paid({"t": [{"wrong": 1}, {"title": "c"}]}, name="claude_cli")
    LLMRouter([claude], ledger, "r").complete(req(Out), "s")
    rows = ledger.run_rows("r")
    assert [r["fallback_reason"] for r in rows] == ["invalid_output", None]


def test_provider_error_in_retry_is_recorded_and_moves_on(tmp_path):
    ledger = CostLedger(tmp_path / "c.db")
    claude, openai = pair([{"wrong": 1}, ProviderError("usage_limit", cost_usd=0.2)])
    router = LLMRouter([claude, openai], ledger, "r")
    result = router.complete(req(Out), "s")
    assert result.provider == "openai" and "usage_limit" in result.fallback_reason
    assert "claude_cli" in router.disabled
    assert any(r["cost_usd"] == 0.2 for r in ledger.run_rows("r"))


def test_build_router(tmp_path):
    s = Settings(_env_file=None, llm_providers=["claude_cli", "openai"], runs_dir=tmp_path)
    router = build_router(s, None, "r1", claude_only=False)
    assert [p.name for p in router._providers] == ["claude_cli", "openai"]
    with pytest.raises(ValueError):
        build_router(Settings(_env_file=None, llm_providers=["bogus"]), None, "r", False)
