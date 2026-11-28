"""Shared image validation (ported unchanged from v1 services/image_service.py)."""
from __future__ import annotations

from io import BytesIO

MIN_VALID_BYTES = 2048  # anything smaller is almost certainly an error page
MIN_PIXEL_VARIANCE = 50  # near-uniform images (blank, all-black) score < 50
MIN_DIM_RATIO = 0.5  # accept anything >= 50% of requested dims


def validate_image(data: bytes, width: int, height: int) -> str | None:
    """Return None if the image is acceptable, else the rejection reason."""
    if len(data) < MIN_VALID_BYTES:
        return f"too few bytes ({len(data)})"
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        img.load()
    except Exception as exc:
        return f"corrupt: {exc}"

    w, h = img.size
    if w < width * MIN_DIM_RATIO or h < height * MIN_DIM_RATIO:
        return f"too small {w}x{h} (wanted {width}x{height})"

    try:
        small = img.convert("L").resize((64, 64))
        pixels = list(small.getdata())
        n = len(pixels)
        mean = sum(pixels) / n
        variance = sum((p - mean) ** 2 for p in pixels) / n
        if variance < MIN_PIXEL_VARIANCE:
            return f"near-uniform pixels (variance {variance:.1f})"
    except Exception:
        # If the variance check itself fails, accept the image; don't reject on tooling bugs.
        pass
    return None
