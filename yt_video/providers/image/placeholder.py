"""Offline PIL gradient placeholder (always available, never cached)."""
from __future__ import annotations

import io


class PlaceholderProvider:
    name = "placeholder"
    model = "pil-gradient"
    cacheable = False
    validated = False  # offline gradient is intentionally near-uniform

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        from PIL import Image, ImageDraw

        top, bottom = (13, 17, 45), (5, 5, 20)  # deep navy -> near-black
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img, "RGBA")
        for y in range(height):
            t = y / height
            color = tuple(int(top[c] + t * (bottom[c] - top[c])) for c in range(3))
            draw.line([(0, y), (width, y)], fill=color)
        grid = (255, 255, 255, 15)
        for x in range(0, width, 80):
            draw.line([(x, 0), (x, height)], fill=grid, width=1)
        for y in range(0, height, 80):
            draw.line([(0, y), (width, y)], fill=grid, width=1)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue()
