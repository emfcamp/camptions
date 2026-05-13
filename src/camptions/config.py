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
    whisper_model: str = "small.en"
    whisper_language: str = "en"
    whisper_use_vad: bool = False
    # Proactive WL reconnect interval in seconds. Must be less than the
    # --max_connection_time set on the WL container (default 3600s).
    wl_reconnect_interval: int = 3300

    # Auth — required in production; unset means the protected endpoints will refuse all requests
    admin_token: str = ""
    ingest_token: str = ""

    # Venues
    default_venues: list[str] = ["stage-a", "stage-b", "stage-c"]

    # Retention
    caption_retention_hours: int = 72

    # Public-API rate limits (per client IP, sliding window).
    # Applies to /api/captions/*, /api/venues/*, /api/sessions/*, /api/schedule/*.
    # Set to 0 to disable. The Pi audio ingest and admin endpoints are NOT
    # rate-limited — admin is token-protected and the Pi is on a known LAN.
    rate_limit_per_minute: int = 120
    # Simultaneous WebSocket connections per IP across all venues.
    ws_connections_per_ip: int = 10


settings = Settings()
