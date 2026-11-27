"""Pollinations.ai provider (free)."""
from __future__ import annotations

from urllib.parse import quote

import requests
from yt_video.logging import get_logger

logger = get_logger(__name__)


class PollinationsProvider:
    name = "pollinations"

    def __init__(self, model: str, key: str | None = None, http_get=requests.get) -> None:
        self.model = model
        self._key = key
        self._get = http_get

    def available(self) -> bool:
        return True

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        url = (
            f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
            f"?width={width}&height={height}&model={self.model}&seed={seed}&nologo=true"
        )
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            response = self._get(url, headers=headers, timeout=90)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type:
                raise ValueError(f"unexpected content-type: {content_type}")
            return response.content
        except Exception as exc:
            logger.warning("pollinations fetch failed: %s", exc)
            return None
