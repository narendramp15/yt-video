"""Deterministic in-memory LLM for tests."""
from __future__ import annotations

import json

from yt_video.providers.llm.base import LLMRequest, RawCompletion, extract_json


class FakeLLM:
    def __init__(self, responses: dict[str, dict | str | Exception], name: str = "fake") -> None:
        self.name = name
        self.responses = responses
        self.requests: list[LLMRequest] = []

    def available(self) -> bool:
        return True

    def complete(self, req: LLMRequest) -> RawCompletion:
        self.requests.append(req)
        value = self.responses[req.task]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            text, data = json.dumps(value), value
        else:
            text, data = value, extract_json(value)
        return RawCompletion(
            provider=self.name, model="fake", text=text, data=data,
            input_tokens=0, output_tokens=0, cost_usd=0.0, notional_cost_usd=None,
        )
