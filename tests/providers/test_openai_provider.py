from types import SimpleNamespace

import httpx
import openai
import pytest

from yt_video.costs import estimate_openai_cost
from yt_video.models import ContentAngle
from yt_video.providers.llm.base import LLMError, LLMRequest, ProviderError, extract_json
from yt_video.providers.llm.fake import FakeLLM
from yt_video.providers.llm.openai_api import OpenAIProvider

PRICING = {"gpt-6-luna": (0.10, 0.50)}


class _Stub:
    def __init__(self, response=None, exc=None):
        self.kw = None
        self._response, self._exc = response, exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kw = kw
        if self._exc:
            raise self._exc
        return self._response


def _resp(content='{"angle":"x"}', usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5) if usage else None,
    )


def _req(schema=ContentAngle):
    return LLMRequest(task="a", system="s", prompt="p", schema=schema, tier="light")


def _provider(stub):
    return OpenAIProvider("key", "gpt-6-luna", PRICING, client_factory=lambda **_: stub)


def test_openai_structured_call():
    stub = _Stub(_resp())
    out = _provider(stub).complete(_req())
    assert stub.kw["response_format"]["json_schema"]["strict"] is True
    assert stub.kw["response_format"]["json_schema"]["name"] == "a"
    assert out.data == {"angle": "x"}
    assert out.provider == "openai" and out.notional_cost_usd is None
    assert (out.input_tokens, out.output_tokens) == (10, 5)
    assert out.cost_usd == estimate_openai_cost("gpt-6-luna", 10, 5, PRICING)


def test_openai_no_schema_and_no_usage():
    out = _provider(_Stub(_resp("hello", usage=False))).complete(_req(None))
    assert out.text == "hello" and out.data is None
    assert out.input_tokens is None and out.cost_usd == 0.0


def test_openai_invalid_output():
    with pytest.raises(ProviderError) as ei:
        _provider(_Stub(_resp("not json"))).complete(_req())
    assert ei.value.kind == "invalid_output"


def test_invalid_output_carries_billed_cost():
    with pytest.raises(ProviderError) as ei:
        _provider(_Stub(_resp("not json"))).complete(_req())
    e = ei.value
    assert e.cost_usd == estimate_openai_cost("gpt-6-luna", 10, 5, PRICING)
    assert (e.input_tokens, e.output_tokens, e.model) == (10, 5, "gpt-6-luna")


def test_missing_pricing_blocks_call():
    stub = _Stub(_resp())
    p = OpenAIProvider("key", "other-model", PRICING, client_factory=lambda **_: stub)
    with pytest.raises(ProviderError) as ei:
        p.complete(_req())
    assert ei.value.kind == "unavailable"
    assert stub.kw is None


def test_openai_unavailable_without_key():
    p = OpenAIProvider(None, "gpt-6-luna", PRICING)
    assert not p.available()
    with pytest.raises(ProviderError) as ei:
        p.complete(_req())
    assert ei.value.kind == "unavailable"


def test_rate_limit_maps_to_usage_limit():
    r = httpx.Response(429, request=httpx.Request("POST", "https://x"))
    exc = openai.RateLimitError("slow down", response=r, body=None)
    with pytest.raises(ProviderError) as ei:
        _provider(_Stub(exc=exc)).complete(_req())
    assert ei.value.kind == "usage_limit"


def test_other_sdk_error_maps_to_api():
    with pytest.raises(ProviderError) as ei:
        _provider(_Stub(exc=openai.OpenAIError("boom"))).complete(_req())
    assert ei.value.kind == "api"


def test_extract_json_variants():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('{"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    assert extract_json("no json here") is None
    assert extract_json('[1,2] {bad} {"a": 2}') == {"a": 2}
    assert extract_json("") is None


def test_llm_error_message():
    e = LLMError([("claude_cli", "timeout"), ("openai", "api")])
    assert str(e) == "claude_cli: timeout; openai: api"
    assert e.attempts[0] == ("claude_cli", "timeout")


def test_fake_llm():
    boom = ProviderError("exit", "bad")
    fake = FakeLLM({"a": {"angle": "x"}, "b": 'noise {"k": 1}', "c": boom})
    assert fake.available()
    r = fake.complete(_req())
    assert r.data == {"angle": "x"} and r.text == '{"angle": "x"}'
    assert (r.provider, r.model, r.cost_usd, r.input_tokens) == ("fake", "fake", 0.0, 0)
    b = LLMRequest("b", "s", "p", None, "light")
    assert fake.complete(b).data == {"k": 1}
    with pytest.raises(ProviderError):
        fake.complete(LLMRequest("c", "s", "p", None, "light"))
    assert len(fake.requests) == 3
