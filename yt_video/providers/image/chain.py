"""Free-first image provider chain with a per-provider on-disk cache."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from yt_video.costs import CostLedger
from yt_video.providers.image.base import ImageProvider, ImageResult, NonRetryableProviderError
from yt_video.providers.image.cloudflare import CloudflareProvider
from yt_video.providers.image.openai_image import OpenAIImageProvider
from yt_video.providers.image.placeholder import PlaceholderProvider
from yt_video.providers.image.pollinations import PollinationsProvider
from yt_video.providers.image.validate import validate_image
from yt_video.settings import Settings
from yt_video.logging import get_logger

logger = get_logger(__name__)


class ImageChain:
    def __init__(
        self,
        providers: list[ImageProvider],
        cache_dir: Path,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.providers = providers
        self.cache_dir = Path(cache_dir)
        self.retries = retries
        self._sleep = sleep
        self.provider_counts: dict[str, int] = {}
        self.disabled: dict[str, int] = {}

    @staticmethod
    def _key(provider: ImageProvider, prompt: str, width: int, height: int, seed: int) -> str:
        raw = f"{provider.name}|{provider.model}|{prompt}|{width}x{height}|{seed}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _fetch(self, provider: ImageProvider, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        """Call one provider with retry + backoff (v1: 3 attempts, 5s * attempt)."""
        check = getattr(provider, "validated", True)
        for attempt in range(1, self.retries + 1):
            try:
                data = provider.generate(prompt, width, height, seed)
            except NonRetryableProviderError as exc:
                self.disabled[provider.name] = exc.status
                logger.warning(
                    "image provider %s disabled for this run: HTTP %d (not retryable)",
                    provider.name, exc.status,
                )
                return None
            if data:
                reason = validate_image(data, width, height) if check else None
                if reason is None:
                    return data
                logger.warning("%s attempt %d/%d: validation failed: %s", provider.name, attempt, self.retries, reason)
            else:
                logger.warning("%s attempt %d/%d: no image returned", provider.name, attempt, self.retries)
            if attempt < self.retries:
                self._sleep(5 * attempt)
        return None

    def _count(self, name: str) -> None:
        self.provider_counts[name] = self.provider_counts.get(name, 0) + 1

    def generate(self, prompt: str, width: int, height: int, seed: int, out_path: Path) -> ImageResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        for provider in self.providers:
            if not provider.available() or provider.name in self.disabled:
                continue
            cacheable = getattr(provider, "cacheable", True)
            cache_file = self.cache_dir / f"{self._key(provider, prompt, width, height, seed)}.img"
            if cacheable and cache_file.is_file():
                cached = cache_file.read_bytes()
                reason = validate_image(cached, width, height)
                if reason is None:
                    shutil.copyfile(cache_file, out_path)
                    self._count(provider.name)
                    return ImageResult(out_path, provider.name, True)
                logger.warning("discarding invalid cache entry %s: %s", cache_file.name, reason)
                cache_file.unlink(missing_ok=True)
            data = self._fetch(provider, prompt, width, height, seed)
            if data is None:
                logger.warning("image provider %s failed; trying next", provider.name)
                continue
            if cacheable:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache_file.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, cache_file)
            out_path.write_bytes(data)
            self._count(provider.name)
            return ImageResult(out_path, provider.name, False)
        raise RuntimeError("all image providers failed or were unavailable")


def build_image_chain(settings: Settings, ledger: CostLedger | None = None, run_id: str = "") -> ImageChain:
    providers: list[ImageProvider] = []
    for name in settings.image_providers:
        if name == "pollinations":
            providers.append(PollinationsProvider(
                settings.pollinations_model, settings.pollinations_key,
                min_interval_s=settings.pollinations_min_interval_s,
            ))
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
