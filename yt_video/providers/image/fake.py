"""Deterministic in-memory image provider for tests."""
from __future__ import annotations

import io
import random


class FakeImageProvider:
    model = "fake"

    def __init__(self, name: str = "fake", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        """Seeded noise PNG at the requested size, so it passes validate_image."""
        self.calls += 1
        if self.fail:
            return None
        from PIL import Image

        rng = random.Random(seed)
        img = Image.frombytes("RGB", (width, height), rng.randbytes(width * height * 3))
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
