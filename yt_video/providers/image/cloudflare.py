"""Cloudflare Workers AI (flux-1-schnell) provider."""
from __future__ import annotations

import base64
import io
import logging

import requests

logger = logging.getLogger(__name__)

_MODEL = "@cf/black-forest-labs/flux-1-schnell"


class CloudflareProvider:
    name = "cloudflare"
    model = _MODEL

    def __init__(self, account_id: str | None, api_token: str | None, http_post=requests.post) -> None:
        self._account_id = account_id
        self._token = api_token
        self._post = http_post

    def available(self) -> bool:
        return bool(self._account_id and self._token)

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run/{_MODEL}"
        try:
            response = self._post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"prompt": prompt, "steps": 4},
                timeout=90,
            )
            response.raise_for_status()
            raw = base64.b64decode(response.json()["result"]["image"])
            return _fit(raw, width, height)
        except Exception as exc:
            logger.warning("cloudflare fetch failed: %s", exc)
            return None


def _fit(raw: bytes, width: int, height: int) -> bytes:
    """Cover-resize and center-crop to exactly width x height; return JPEG bytes."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    scale = max(width / img.width, height / img.height)
    new_size = (max(width, round(img.width * scale)), max(height, round(img.height * scale)))
    img = img.resize(new_size, Image.LANCZOS)
    left, top = (img.width - width) // 2, (img.height - height) // 2
    img = img.crop((left, top, left + width, top + height))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
