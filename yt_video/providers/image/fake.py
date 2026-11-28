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
        """Seeded PNG of random coarse colour blocks at the requested size.

        Coarse blocks (upscaled with NEAREST) keep their contrast after
        validate_image's 64x64 downsample, unlike per-pixel noise which averages
        to near-uniform grey at large sizes.
        """
        self.calls += 1
        if self.fail:
            return None
        from PIL import Image

        rng = random.Random(seed)
        gw, gh = min(48, width), min(27, height)
        grid = Image.frombytes("RGB", (gw, gh), rng.randbytes(gw * gh * 3))
        img = grid.resize((width, height), Image.NEAREST)
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
