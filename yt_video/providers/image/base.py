"""Image provider protocol and chain result."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ImageProvider(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        """Return raw image bytes, or None on failure. No retry, validation or caching."""
        ...


class NonRetryableProviderError(Exception):
    """A provider failure that retrying cannot fix (auth, paywall, missing model)."""

    def __init__(self, provider: str, status: int) -> None:
        super().__init__(f"{provider}: HTTP {status}")
        self.provider = provider
        self.status = status


NON_RETRYABLE_STATUSES = frozenset({401, 402, 403, 404})


@dataclass(frozen=True)
class ImageResult:
    path: Path
    provider: str
    cached: bool
