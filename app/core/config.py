import os
import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A plain DNS name with at least one dot: nothing that could break out of a CSP source list.
_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/?$")
R2_VARS = ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL")


def _clean_https(value: str, name: str, *, allow_path: bool) -> str:
    """Rebuild an https URL from its checked parts; never echo the value in an error."""
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"{name} has an invalid port") from None
    if parts.scheme != "https" or not _HOST_RE.fullmatch(host):
        raise ValueError(f"{name} must be an https:// URL with a plain host name")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f"{name} must be a plain https:// URL without credentials or query")
    path = parts.path
    if not _PATH_RE.fullmatch(path) or (path.strip("/") and not allow_path):
        raise ValueError(f"{name} has an invalid path")
    return f"https://{host}" + (f":{port}" if port is not None else "") + path.rstrip("/")


class Settings(BaseSettings):
    """Application settings, read from environment variables (and `.env` in development)."""

    # hide_input_in_errors: a failed setting is reported by name only, so a startup error can
    # never print a secret (SECRET_KEY, R2 keys) or a URL with credentials in it.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "UstoiMan"  # working brand name, exactly this casing (R-023)
    secret_key: SecretStr = Field(min_length=32)
    database_url: PostgresDsn
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    log_level: str = "INFO"
    # Header a trusted proxy fills with the visitor's address, e.g. CF-Connecting-IP on Render
    # (Cloudflare in front). Unset: the connection address is used. Set it only when every
    # request passes through that proxy, or visitors could forge it (R-019).
    client_ip_header: str | None = None
    # Google sign-in (BRIEF §4.1). Both must be set to show the Google button.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Telegram bot: sign-in, linking and phone sharing. The username alone shows the Telegram
    # buttons on the site; the token, webhook secret and URL are used only by the bot process.
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_url: str | None = None
    # Public address of the site, no trailing slash; the bot builds absolute links from it.
    site_url: str = "http://localhost:8000"
    # Uploaded images (avatars, portfolio) are stored here when R2 is not configured.
    media_root: str = "media"
    # Cloudflare R2 (spec 2026-11-23 §D8). All five set: images go to the bucket and are served
    # from R2_PUBLIC_URL (no trailing slash); otherwise local disk. Only the site needs them.
    r2_endpoint: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str | None = None
    r2_public_url: str | None = None
    # None means "decide by APP_ENV": on in development and test, off on staging and production
    # (Render's disk is ephemeral) unless set explicitly. Off hides upload controls, POSTs are 404.
    uploads_enabled: bool | None = None
    # Sentry error monitoring (PRODUCT_RULES A3, R-024): on only when SENTRY_DSN is set.
    # The release defaults to Render's RENDER_GIT_COMMIT when SENTRY_RELEASE is not set.
    sentry_dsn: SecretStr | None = None
    sentry_release: str | None = None
    plan_limits: dict[str, dict[str, int]] = {
        "free": {
            "collections": 30,
            "photos_per_collection": 60,
            "specialties": 10,
            "primary_specialties": 3,
            "areas": 20,
        }
    }

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

    @field_validator(
        "r2_endpoint",
        "r2_access_key_id",
        "r2_secret_access_key",
        "r2_bucket",
        "r2_public_url",
        "sentry_dsn",
        "sentry_release",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("r2_endpoint", "r2_public_url")
    @classmethod
    def _https_url(cls, value: str | None, info) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        name = info.field_name.upper()
        # The endpoint is the account's S3 address: a host only. The public URL may have a path.
        return _clean_https(value, name, allow_path=name == "R2_PUBLIC_URL")

    @model_validator(mode="after")
    def _add_platform_host(self) -> "Settings":
        # Render sets RENDER_EXTERNAL_HOSTNAME for each service; trust it automatically.
        host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if host and host not in self.allowed_hosts:
            self.allowed_hosts = [*self.allowed_hosts, host]
        if self.sentry_release is None:
            self.sentry_release = os.environ.get("RENDER_GIT_COMMIT", "").strip() or None
        if self.uploads_enabled is None:
            self.uploads_enabled = not self.is_production_like
        # Uploads on staging/production without R2 would land on Render's wiped disk: refuse.
        missing = self.r2_missing
        if self.is_production_like and self.uploads_enabled and missing:
            raise ValueError(
                "UPLOADS_ENABLED is true on staging/production but R2 is not fully configured; "
                "missing: " + ", ".join(missing)
            )
        if self.app_env == "production":
            parts = urlsplit(self.site_url)
            if parts.scheme != "https" or (parts.hostname or "").lower() in {
                "localhost",
                "127.0.0.1",
                "::1",
                "0.0.0.0",  # noqa: S104
            }:
                raise ValueError("SITE_URL must be a public https:// URL in production")
        return self

    def limit(self, name: str, plan: str = "free") -> int:
        return self.plan_limits[plan][name]

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def telegram_enabled(self) -> bool:
        # The site only links to the bot, so the username is enough; the token lives only on
        # the bot service (least privilege, R-021 D8).
        return bool(self.telegram_bot_username)

    @property
    def r2_missing(self) -> list[str]:
        """Names (never values) of the R2 variables that are not set."""
        values = (
            self.r2_endpoint,
            self.r2_access_key_id,
            self.r2_secret_access_key,
            self.r2_bucket,
            self.r2_public_url,
        )
        return [name for name, value in zip(R2_VARS, values, strict=True) if value is None]

    @property
    def r2_enabled(self) -> bool:
        return not self.r2_missing

    @property
    def r2_public_origin(self) -> str | None:
        """`https://host[:port]` of the public bucket URL (for the CSP), or None when R2 is off.

        Built from the validated host and port, never from the raw netloc.
        """
        if not self.r2_enabled or self.r2_public_url is None:
            return None
        parts = urlsplit(self.r2_public_url)
        host = (parts.hostname or "").lower()
        return f"https://{host}" + (f":{parts.port}" if parts.port is not None else "")

    @property
    def secure_cookies(self) -> bool:
        # Staging and production are HTTPS, so cookies get the __Host- prefix and Secure.
        # Development and tests run on plain http://localhost, where the prefix would be
        # refused by some browsers; they use the same names without the prefix (dev-only).
        return self.is_production_like


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
