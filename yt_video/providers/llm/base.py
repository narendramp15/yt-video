"""LLM provider contract: request/response types, errors, JSON extraction."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel

ErrorKind = Literal["unavailable", "exit", "timeout", "usage_limit", "invalid_output", "api"]


@dataclass
class LLMRequest:
    task: str
    system: str
    prompt: str
    schema: type[BaseModel] | None
    tier: Literal["script", "light"]
    max_output_tokens: int = 8000
    timeout_s: int | None = None  # None: provider uses its tier default


@dataclass
class RawCompletion:
    provider: str
    model: str
    text: str
    data: dict | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float
    notional_cost_usd: float | None


@dataclass
class LLMResult(RawCompletion):
    parsed: BaseModel | None = None
    fallback_reason: str | None = None


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind: ErrorKind = kind


class LLMError(Exception):
    def __init__(self, attempts: list[tuple[str, str]]) -> None:
        super().__init__("; ".join(f"{p}: {r}" for p, r in attempts))
        self.attempts = attempts


class LLMProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, req: LLMRequest) -> RawCompletion: ...


def extract_json(text: str) -> dict | None:
    """First decodable top-level JSON object in ``text``; never raises."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    pos = text.find("{")
    while pos != -1:
        try:
            obj, _ = decoder.raw_decode(text, pos)
        except ValueError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(obj, dict):
            return obj
        pos = text.find("{", pos + 1)
    return None
