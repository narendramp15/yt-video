"""RunContext: what every stage receives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from yt_video.pipeline.run_store import Run

if TYPE_CHECKING:
    from yt_video.costs import CostLedger
    from yt_video.integrations.youtube import YouTubeClient
    from yt_video.media.clip import ClipVerifier
    from yt_video.memory.analytics_memory import AnalyticsMemory
    from yt_video.memory.tts_memory import TTSMemory
    from yt_video.providers.image.chain import ImageChain
    from yt_video.providers.llm.router import LLMRouter
    from yt_video.settings import Settings

T = TypeVar("T", bound=BaseModel)


@dataclass
class RunContext:
    settings: Settings
    run: Run
    router: LLMRouter
    images: ImageChain
    clip: ClipVerifier
    tts_memory: TTSMemory
    analytics: AnalyticsMemory
    youtube: YouTubeClient | None
    ledger: CostLedger
    no_upload: bool
    today: date

    def load(self, stage: str, model: type[T]) -> T:
        """Validate a stage's stored data; raises if the stage has none."""
        data = self.run.get(stage)
        if not data:
            raise ValueError(f"stage {stage!r} has no stored data in run {self.run.id}")
        return model.model_validate(data)
