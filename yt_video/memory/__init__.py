"""Self-improving feedback loops: TTS style memory and YouTube analytics memory."""

from yt_video.memory.analytics_memory import (
    AnalyticsMemory,
    detect_weak_retention_bands,
    summarize_retention_curve,
)
from yt_video.memory.tts_memory import TTSMemory

__all__ = [
    "AnalyticsMemory",
    "TTSMemory",
    "detect_weak_retention_bands",
    "summarize_retention_curve",
]
