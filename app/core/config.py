import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (and `.env` in development)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Master COM"
    secret_key: SecretStr = Field(min_length=32)
    database_url: PostgresDsn
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    log_level: str = "INFO"
    # Google sign-in (BRIEF §4.1). Both must be set to show the Google button.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Hosts hand out postgres:// or postgresql:// URLs;
        # SQLAlchemy needs the psycopg 3 driver name.
        if isinstance(value, str):
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @model_validator(mode="after")
    def _add_platform_host(self) -> "Settings":
        # Render sets RENDER_EXTERNAL_HOSTNAME for each service; trust it automatically.
        host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if host and host not in self.allowed_hosts:
            self.allowed_hosts = [*self.allowed_hosts, host]
        return self

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def secure_cookies(self) -> bool:
        # Staging and production are HTTPS, so cookies get the __Host- prefix and Secure.
        # Development and tests run on plain http://localhost, where the prefix would be
        # refused by some browsers; they use the same names without the prefix (dev-only).
        return self.is_production_like


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
