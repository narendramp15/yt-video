"""OpenAI Images provider (PAID, opt-in only; never in the default chain)."""
from __future__ import annotations

import base64
import logging

import requests

from yt_video.costs import CostLedger

logger = logging.getLogger(__name__)

# v1 recorded no image cost. Flat per-image ESTIMATE (gpt-image-1, low quality, ~1024px);
# actual billing varies by model/quality/size. Update if OpenAI pricing changes.
OPENAI_IMAGE_COST_USD = 0.02


def openai_image_size(width: int, height: int) -> str:
    """Nearest size gpt-image-1 supports: 1536x1024 / 1024x1536 / 1024x1024."""
    ratio = width / height if height else 1.0
    if ratio > 1.2:
        return "1536x1024"
    if ratio < 0.83:
        return "1024x1536"
    return "1024x1024"


class OpenAIImageProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        quality: str,
        ledger: CostLedger | None = None,
        run_id: str = "",
        stage: str = "images",
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._quality = quality
        self._ledger = ledger
        self._run_id = run_id
        self._stage = stage

    def available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        """`seed` is unsupported by the Images API and ignored."""
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("openai package not installed; skipping openai image provider")
            return None
        size = openai_image_size(width, height)
        try:
            result = OpenAI(api_key=self._api_key).images.generate(
                model=self.model, prompt=prompt, size=size, quality=self._quality, n=1
            )
            item = result.data[0]
            b64 = getattr(item, "b64_json", None)
            if b64:
                data = base64.b64decode(b64)
            else:
                url = getattr(item, "url", None)
                if not url:
                    logger.warning("openai image response had no image data")
                    return None
                r = requests.get(url, timeout=90)
                r.raise_for_status()
                data = r.content
        except Exception as exc:
            logger.warning("openai image generation failed: %s", exc)
            return None
        if self._ledger is not None:
            self._ledger.record(
                self._run_id, self._stage, "image", self.name, self.model,
                None, None, OPENAI_IMAGE_COST_USD,
            )
        return data
