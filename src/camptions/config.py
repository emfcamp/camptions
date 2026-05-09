"""Configuration management using Pydantic Settings."""

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

    # WhisperLiveKit sidecar — runs as a separate container.
    # Model, language, backend etc. are configured on the WLK container itself.
    wlk_url: str = "ws://wlk:8000/asr"

    # Venues
    default_venues: list[str] = ["stage-a", "stage-b", "stage-c"]

    # Retention
    caption_retention_hours: int = 72


settings = Settings()
