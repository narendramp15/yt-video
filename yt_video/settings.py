"""Typed settings loaded once from .env. No import-time side effects."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

CommaList = Annotated[list[str], NoDecode]


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [p.strip().lower() for p in value.split(",") if p.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
        env_ignore_empty=True,
    )

    # Core
    openai_api_key: str | None = None
    youtube_client_secret: Path = PROJECT_ROOT / "client_secret.json"
    channel_name: str | None = None
    default_category: str = "22"

    # Video packaging / retention craft
    chapter_cards_enabled: bool = False
    intro_card_enabled: bool = False
    lower_thirds_enabled: bool = False
    thumbnail_text_enabled: bool = True
    burn_captions_enabled: bool = True
    shorts_enabled: bool = True
    channel_primary_niche: CommaList = []
    packaging_variants: int = 3
    niche_lock_enabled: bool = True
    niche_lock_days: int = 30
    packaging_require_approval: bool = False

    # LLM providers
    llm_providers: CommaList = ["claude_cli", "openai"]
    claude_cli_path: str = "claude"
    claude_model_script: str = "sonnet"
    claude_model_light: str = "haiku"
    claude_timeout_script_s: int = 600
    claude_timeout_light_s: int = 180
    openai_fallback_model: str = "gpt-6-luna"
    # model -> (input $/1M tokens, output $/1M tokens)
    openai_pricing: dict[str, tuple[float, float]] = {
        "gpt-6-luna": (0.10, 0.50),
        "gpt-5-nano": (0.05, 0.40),
        "gpt-4o-mini": (0.15, 0.60),
    }

    # TTS
    edge_tts_voice: str = "en-IN-NeerjaNeural"
    edge_tts_rate: str = "-8%"
    edge_tts_pitch: str = "+1Hz"

    # Images
    image_providers: CommaList = ["pollinations", "cloudflare", "placeholder"]
    pollinations_model: str = "flux"
    pollinations_key: str | None = None
    pollinations_min_interval_s: float = 45.0
    openai_image_model: str = "gpt-image-1"
    openai_image_quality: str = "low"
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    clip_verify: bool = True
    clip_min_score: float = 0.24
    clip_model: str = "openai/clip-vit-base-patch32"

    # Background music
    background_music_enabled: bool = True
    background_music_track: Path | None = None
    background_music_dir: Path = PROJECT_ROOT / "assets" / "music"

    # X / Twitter trends + posting
    twitter_bearer_token: str | None = None
    twitter_woeid: int = 23424848
    x_consumer_key: str | None = None
    x_consumer_secret: str | None = None
    x_access_token: str | None = None
    x_access_token_secret: str | None = None
    x_daily_post_limit: int = 3
    x_post_public_only: bool = True

    # Paths
    state_dir: Path = PROJECT_ROOT / "state"
    runs_dir: Path = PROJECT_ROOT / "output" / "runs"
    image_cache_dir: Path = PROJECT_ROOT / "output" / "cache" / "images"

    @field_validator(
        "llm_providers", "image_providers", "channel_primary_niche", mode="before"
    )
    @classmethod
    def _parse_csv(cls, v: Any) -> Any:
        return _split_csv(v)

    @field_validator("packaging_variants", "niche_lock_days", mode="after")
    @classmethod
    def _at_least_one(cls, v: int) -> int:
        return max(1, v)

    @field_validator(
        "youtube_client_secret",
        "background_music_track",
        "background_music_dir",
        "state_dir",
        "runs_dir",
        "image_cache_dir",
        mode="after",
    )
    @classmethod
    def _anchor_to_root(cls, v: Path | None) -> Path | None:
        if v is None or v.is_absolute():
            return v
        return PROJECT_ROOT / v


def load_settings(env_file: Path | None = None, **overrides: Any) -> Settings:
    """Load settings from ``env_file`` (default ``PROJECT_ROOT/.env``).

    Keyword overrides win over the environment and the file.
    """
    path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    return Settings(_env_file=path, **overrides)  # type: ignore[call-arg]
