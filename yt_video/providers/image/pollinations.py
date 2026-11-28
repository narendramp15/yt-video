"""Pollinations.ai provider (free tier is rate limited to 1 image / 45 s / IP)."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import quote

import requests

from yt_video.logging import get_logger
from yt_video.providers.image.base import NON_RETRYABLE_STATUSES, NonRetryableProviderError

logger = get_logger(__name__)

_RATE_LIMIT_STATUSES = frozenset({402, 429})
_DEFAULT_WAIT_S = 45.0
_WAIT_MARGIN_S = 2.0
_WAIT_CAP_S = 90.0
_MAX_RATE_WAITS = 3
_RETRY_AFTER_RE = re.compile(r"retry after (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def _rate_limit_wait(response) -> float | None:
    """Seconds to wait if ``response`` is a free-tier rate limit, else None."""
    if getattr(response, "status_code", None) not in _RATE_LIMIT_STATUSES:
        return None
    body = getattr(response, "text", "") or ""
    if "FREE_LIMIT" not in body and "retry after" not in body.lower():
        return None
    match = _RETRY_AFTER_RE.search(body)
    seconds = float(match.group(1)) if match else _DEFAULT_WAIT_S
    return min(seconds + _WAIT_MARGIN_S, _WAIT_CAP_S)


class PollinationsProvider:
    name = "pollinations"

    def __init__(
        self,
        model: str,
        key: str | None = None,
        http_get=requests.get,
        min_interval_s: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self._key = key
        self._get = http_get
        self._min_interval_s = 0.0 if key else min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def available(self) -> bool:
        return True

    def _pace(self) -> None:
        if self._min_interval_s > 0 and self._last_request is not None:
            wait = self._min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def generate(self, prompt: str, width: int, height: int, seed: int) -> bytes | None:
        url = (
            f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
            f"?width={width}&height={height}&model={self.model}&seed={seed}&nologo=true"
        )
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            for rate_waits in range(_MAX_RATE_WAITS + 1):
                self._pace()
                response = self._get(url, headers=headers, timeout=90)
                wait = _rate_limit_wait(response)
                if wait is None:
                    break
                if rate_waits == _MAX_RATE_WAITS:
                    raise RuntimeError("rate limited after retries")
                logger.warning("pollinations rate limited; waiting %.0fs", wait)
                self._sleep(wait)
            status = getattr(response, "status_code", None)
            if status in NON_RETRYABLE_STATUSES:
                raise NonRetryableProviderError(self.name, status)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type:
                raise ValueError(f"unexpected content-type: {content_type}")
            return response.content
        except NonRetryableProviderError:
            raise
        except Exception as exc:
            logger.warning("pollinations fetch failed: %s", exc)
            return None
