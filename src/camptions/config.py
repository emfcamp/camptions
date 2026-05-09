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

    # WhisperLive sidecar — runs as a separate container.
    wl_url: str = "ws://wl:9090"
    whisper_model: str = "medium.en"
    whisper_language: str = "en"
    whisper_use_vad: bool = False
    # Proactive WL reconnect interval in seconds. Must be less than the
    # --max_connection_time set on the WL container (default 3600s).
    wl_reconnect_interval: int = 3300

    # Venues
    default_venues: list[str] = ["stage-a", "stage-b", "stage-c"]

    # Retention
    caption_retention_hours: int = 72


settings = Settings()
