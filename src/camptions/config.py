"""Configuration management using Pydantic Settings."""

from typing import Literal

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(
        env_file=".env",
        env_prefix="CAMPTIONS_",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./camptions.db"

    # WhisperLiveKit
    whisper_model: str = "small"
    whisper_language: str = "en"
    whisper_backend: Literal["auto", "faster-whisper", "whisper"] = "auto"
    whisper_backend_policy: Literal["simulstreaming", "localagreement"] = "simulstreaming"
    enable_diarization: bool = False
    enable_vad: bool = True

    # Venues
    default_venues: list[str] = ["stage-a", "stage-b", "stage-c"]

    # Retention
    caption_retention_hours: int = 72


settings = Settings()
