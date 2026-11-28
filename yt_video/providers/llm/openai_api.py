"""OpenAI chat-completions provider (fallback LLM)."""
from __future__ import annotations

from typing import Any, Callable

import openai

from yt_video.costs import estimate_openai_cost
from yt_video.models import llm_schema
from yt_video.providers.llm.base import (
    LLMRequest,
    ProviderError,
    RawCompletion,
    extract_json,
)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        pricing: dict[str, tuple[float, float]],
        client_factory: Callable[..., Any] = openai.OpenAI,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._pricing = pricing
        self._client_factory = client_factory

    def available(self) -> bool:
        return self._api_key is not None

    def complete(self, req: LLMRequest) -> RawCompletion:
        if not self.available():
            raise ProviderError("unavailable", "OPENAI_API_KEY not set")
        if self._model not in self._pricing:
            raise ProviderError(
                "unavailable", f"no pricing configured for model {self._model}"
            )
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.prompt},
            ],
            "max_completion_tokens": req.max_output_tokens,
        }
        if req.timeout_s is not None:
            kwargs["timeout"] = req.timeout_s
        if req.schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": req.task,
                    "schema": llm_schema(req.schema),
                    "strict": True,
                },
            }
        try:
            client = self._client_factory(api_key=self._api_key)
            resp = client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise ProviderError("usage_limit", str(exc)) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("api", str(exc)) from exc

        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", None) if usage else None
        out_tok = getattr(usage, "completion_tokens", None) if usage else None
        cost = 0.0
        if in_tok is not None and out_tok is not None:
            cost = estimate_openai_cost(self._model, in_tok, out_tok, self._pricing)
        data = None
        if req.schema is not None:
            data = extract_json(text)
            if data is None:
                finish = getattr(choice, "finish_reason", None)
                detail = f" (finish_reason={finish})" if finish else ""
                raise ProviderError(
                    "invalid_output",
                    f"no JSON object in OpenAI response{detail}",
                    cost_usd=cost,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    model=self._model,
                )
        return RawCompletion(
            provider=self.name,
            model=self._model,
            text=text,
            data=data,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            notional_cost_usd=None,
        )
