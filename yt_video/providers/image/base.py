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


@dataclass(frozen=True)
class ImageResult:
    path: Path
    provider: str
    cached: bool
