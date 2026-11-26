"""Free-first image provider chain with a per-provider on-disk cache."""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from yt_video.costs import CostLedger
from yt_video.providers.image.base import ImageProvider, ImageResult
from yt_video.providers.image.cloudflare import CloudflareProvider
from yt_video.providers.image.openai_image import OpenAIImageProvider
from yt_video.providers.image.placeholder import PlaceholderProvider
from yt_video.providers.image.pollinations import PollinationsProvider
from yt_video.settings import Settings

logger = logging.getLogger(__name__)


class ImageChain:
    def __init__(self, providers: list[ImageProvider], cache_dir: Path) -> None:
        self.providers = providers
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def _key(provider: ImageProvider, prompt: str, width: int, height: int, seed: int) -> str:
        raw = f"{provider.name}|{provider.model}|{prompt}|{width}x{height}|{seed}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path) -> ImageResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        for provider in self.providers:
            if not provider.available():
                continue
            cacheable = getattr(provider, "cacheable", True)
            cache_file = self.cache_dir / f"{self._key(provider, prompt, width, height, seed)}.img"
            if cacheable and cache_file.is_file():
                shutil.copyfile(cache_file, out_path)
                return ImageResult(out_path, provider.name, True)
            data = provider.generate(prompt, width, height, seed)
            if not data:
                logger.warning("image provider %s returned nothing; trying next", provider.name)
                continue
            if cacheable:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(data)
            out_path.write_bytes(data)
            return ImageResult(out_path, provider.name, False)
        raise RuntimeError("all image providers failed or were unavailable")


def build_image_chain(settings: Settings, ledger: CostLedger | None = None, run_id: str = "") -> ImageChain:
    providers: list[ImageProvider] = []
    for name in settings.image_providers:
        if name == "pollinations":
            providers.append(PollinationsProvider(settings.pollinations_model, settings.pollinations_key))
        elif name == "cloudflare":
            providers.append(CloudflareProvider(settings.cloudflare_account_id, settings.cloudflare_api_token))
        elif name == "placeholder":
            providers.append(PlaceholderProvider())
        elif name == "openai":
            providers.append(OpenAIImageProvider(
                settings.openai_api_key, settings.openai_image_model, settings.openai_image_quality,
                ledger=ledger, run_id=run_id,
            ))
        else:
            raise ValueError(f"unknown image provider: {name!r}")
    return ImageChain(providers, settings.image_cache_dir)
