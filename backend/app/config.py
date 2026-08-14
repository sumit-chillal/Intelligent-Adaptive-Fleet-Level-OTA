"""
CONVOY — configuration.

One place where environment variables become typed, validated Python values.
Nothing else in the backend reads os.environ directly; if a module needs a
setting it imports `settings` from here. That way a missing or malformed value
fails once, loudly, at startup, instead of surfacing as a confusing TypeError
three layers deep during a live demo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The backend runs on the admin machine and shares the admin's .env,
        # so there is exactly one file holding one set of credentials.
        env_file=(REPO_ROOT / "admin" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    convoy_env: str = "local"
    log_level: str = "INFO"

    # ---- broker ------------------------------------------------------------
    mqtt_host: str
    mqtt_port: int = 8883
    mqtt_username: str
    mqtt_password: str
    mqtt_transport: str = "tcp"
    mqtt_topic_root: str = "convoy/v1"
    mqtt_shared_group: str = "convoy-bridge"
    mqtt_client_id: str = "convoy-bridge-1"

    # ---- datastores --------------------------------------------------------
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # ---- crypto ------------------------------------------------------------
    firmware_signing_private_key_path: Path | None = None
    firmware_signing_private_key: str | None = None
    firmware_storage_dir: Path = REPO_ROOT / "backend" / "storage" / "firmware"

    # ---- api ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_api_token: str = ""
    cors_origins: str = "http://localhost:3000"

    # ---- rollout defaults --------------------------------------------------
    default_batch_size: int = 5
    default_batch_size_min: int = 1
    default_batch_size_max: int = 20
    default_canary_size: int = 2
    default_min_battery: int = 30
    default_min_network_quality: int = 2
    shrink_threshold: float = 0.20
    abort_threshold: float = 0.40
    grow_after_clean_batches: int = 2
    ewma_alpha: float = 0.5
    chunk_size_bytes: int = 8192
    chunk_window: int = 8
    device_offline_ttl_seconds: int = 20
    batch_timeout_seconds: int = 180
    max_attempts_per_device: int = 3

    @field_validator("mqtt_host")
    @classmethod
    def _clean_host(cls, v: str) -> str:
        """A hostname pasted from a browser often arrives with a scheme or a
        trailing slash. Fixing it here is kinder than a DNS failure."""
        v = v.strip().rstrip("/")
        for prefix in ("https://", "http://", "mqtts://", "mqtt://", "tls://"):
            if v.startswith(prefix):
                v = v[len(prefix):]
        if "/" in v or ":" in v:
            raise ValueError(
                f"MQTT_HOST should be a bare hostname, got {v!r}. "
                "Put the port in MQTT_PORT.")
        return v

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            # The sync driver silently blocks the event loop. Catch it here
            # rather than debugging mysterious stalls under load later.
            raise ValueError(
                "DATABASE_URL must use the async driver: "
                "postgresql+asyncpg://... (not postgresql://...)")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def alembic_database_url(self) -> str:
        """Alembic's migration runner uses a sync driver."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()