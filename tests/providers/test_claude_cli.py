import json
import subprocess
from pathlib import Path

import pytest

from yt_video.models import ContentAngle
from yt_video.providers.llm.base import LLMRequest, ProviderError
from yt_video.providers.llm.claude_cli import (
    ClaudeCLIProvider,
    build_claude_args,
    parse_claude_envelope,
)

FIX = Path(__file__).parent.parent / "fixtures" / "claude"
MODELS = {"script": "sonnet", "light": "haiku"}
TIMEOUTS = {"script": 600, "light": 180}


def _bare(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _wrapped(name):
    d = _bare(name)
    return d["stdout"], d["stderr"], d["returncode"]


def _cp(stdout, stderr="", rc=0):
    return subprocess.CompletedProcess(["claude"], rc, stdout, stderr)


def _provider(tmp_path, run_cmd):
    return ClaudeCLIProvider("claude", MODELS, TIMEOUTS, tmp_path / "scratch", run_cmd)


def _req(schema=None, tier="light", prompt="p", timeout_s=None):
    return LLMRequest(task="t", system="sys ₹", prompt=prompt, schema=schema, tier=tier, timeout_s=timeout_s)


def _capture(stdout, calls):
    def run(args, **kw):
        calls.append((args, kw))
        return _cp(stdout)

    return run


def test_args_never_bare_and_have_isolation_flags(tmp_path):
    args = build_claude_args("claude", "haiku", tmp_path / "s.txt", {"type": "object"})
    assert "--bare" not in args
    for flag in ("--tools", "--no-session-persistence", "-p", "--strict-mcp-config"):
        assert flag in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--json-schema") + 1] == '{"type":"object"}'
    assert "--json-schema" not in build_claude_args("claude", "haiku", tmp_path / "s.txt", None)


def test_parse_success_schema_fixture():
    raw = parse_claude_envelope(json.dumps(_bare("success_schema")), "", 0)
    assert raw.data == {"word": "ok"}
    assert raw.cost_usd == 0.0
    assert raw.notional_cost_usd == _bare("success_schema")["total_cost_usd"]
    assert raw.input_tokens == 2 + 8950 + 0
    assert raw.output_tokens == 234
    assert raw.model == "claude-haiku-5-5"
    assert raw.provider == "claude_cli"


def test_parse_result_with_code_fence():
    env = dict(_bare("success_text"), result='```json\n{"word":"ok"}\n```')
    raw = parse_claude_envelope(json.dumps(env), "", 0)
    assert raw.data == {"word": "ok"}


def test_parse_plain_text_has_no_data():
    raw = parse_claude_envelope(json.dumps(_bare("success_text")), "", 0)
    assert raw.text == "ok" and raw.data is None


def test_error_fixture_raises_exit():
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(*_wrapped("error"))
    assert e.value.kind == "exit"


def test_usage_limit_fixture_raises_usage_limit():
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(*_wrapped("usage_limit"))
    assert e.value.kind == "usage_limit"


@pytest.mark.parametrize(
    "msg", ["API Error: 529 overloaded_error: Overloaded", "Extra usage is required for long context"]
)
def test_overload_and_long_context_are_exit_not_usage_limit(msg):
    env = {"is_error": True, "result": msg, "api_error_status": 529, "subtype": "success"}
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(json.dumps(env), "", 1)
    assert e.value.kind == "exit"


def test_long_context_text_with_429_rate_limit_is_exit():
    env = {
        "is_error": True,
        "result": "Extra usage is required for long context",
        "api_error_status": 429,
        "api_error_type": "rate_limit_error",
    }
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(json.dumps(env), "", 1)
    assert e.value.kind == "exit"


def test_status_429_with_neutral_text_is_usage_limit():
    env = {"is_error": True, "result": "Request failed", "api_error_status": 429}
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(json.dumps(env), "", 1)
    assert e.value.kind == "usage_limit"


def test_is_error_with_returncode_zero_still_errors_and_ignores_subtype():
    env = {"is_error": True, "result": "boom", "subtype": "success"}
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope(json.dumps(env), "", 0)
    assert e.value.kind == "exit"


def test_nonzero_rc_with_limit_text_on_stderr():
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope("not json", "Usage limit reached", 1)
    assert e.value.kind == "usage_limit"


def test_unparseable_stdout_rc0_is_exit_with_excerpt():
    with pytest.raises(ProviderError) as e:
        parse_claude_envelope("garbage" * 100, "", 0)
    assert e.value.kind == "exit" and "garbage" in str(e.value) and len(str(e.value)) < 500


def test_invalid_output_when_schema_and_no_data(tmp_path):
    env = dict(_bare("success_text"), result="no json here")
    p = _provider(tmp_path, _capture(json.dumps(env), []))
    with pytest.raises(ProviderError) as e:
        p.complete(_req(schema=ContentAngle))
    assert e.value.kind == "invalid_output"


def test_timeout_raises_timeout_kind(tmp_path):
    def run(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    with pytest.raises(ProviderError) as e:
        _provider(tmp_path, run).complete(_req())
    assert e.value.kind == "timeout"


def test_missing_binary_unavailable(tmp_path):
    def run(args, **kw):
        raise FileNotFoundError("claude")

    with pytest.raises(ProviderError) as e:
        _provider(tmp_path, run).complete(_req())
    assert e.value.kind == "unavailable"


def test_prompt_sent_as_utf8_and_system_file_written(tmp_path):
    calls = []
    p = _provider(tmp_path, _capture(json.dumps(_bare("success_text")), calls))
    prompt = "₹500 बुमराह 🏏"
    p.complete(_req(prompt=prompt))
    args, kw = calls[0]
    assert kw["input"] == prompt and kw["encoding"] == "utf-8"
    assert kw["capture_output"] is True and kw["text"] is True
    assert kw["cwd"] == tmp_path / "scratch"
    f = tmp_path / "scratch" / "t_system.txt"
    assert f.read_text(encoding="utf-8") == "sys ₹"
    assert args[args.index("--system-prompt-file") + 1] == str(f)


def test_tier_selects_model_and_timeout(tmp_path):
    calls = []
    p = _provider(tmp_path, _capture(json.dumps(_bare("success_text")), calls))
    p.complete(_req(tier="light"))
    p.complete(_req(tier="script"))
    p.complete(_req(tier="script", timeout_s=7))
    assert calls[0][0][calls[0][0].index("--model") + 1] == "haiku"
    assert calls[1][0][calls[1][0].index("--model") + 1] == "sonnet"
    assert [c[1]["timeout"] for c in calls] == [180, 600, 7]


def test_schema_request_passes_json_schema(tmp_path):
    calls = []
    p = _provider(tmp_path, _capture(json.dumps(_bare("success_schema")), calls))
    p.complete(_req(schema=ContentAngle))
    assert "--json-schema" in calls[0][0]


def test_available(tmp_path):
    assert ClaudeCLIProvider("no-such-binary-xyz", MODELS, TIMEOUTS, tmp_path).available() is False
    assert ClaudeCLIProvider("python", MODELS, TIMEOUTS, tmp_path).available() is True
