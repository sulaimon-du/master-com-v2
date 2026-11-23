"""Where uploaded images live. Keys are generated here and never contain user input.

Local disk is used in development and tests; Cloudflare R2 (S3 API) is used when all R2 settings
are present (spec 2026-11-23 §D8), so photos survive deploys on staging and production.
"""

import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings

# ASCII digits only: `\d` would also accept other scripts' digits.
KEY_RE = re.compile(r"^(a|p/[0-9]{4}/[0-9]{2})/[0-9a-f]{32}(-t)?\.webp$")
CACHE_CONTROL = "public, max-age=31536000, immutable"  # keys are random and never rewritten


class StorageError(Exception):
    """The storage backend failed. Carries no backend detail; callers show a friendly error."""


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def url(self, key: str) -> str: ...


def _check(key: str) -> None:
    if not KEY_RE.fullmatch(key):
        raise ValueError("invalid storage key")


class LocalDiskStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def path(self, key: str) -> Path:
        """The file for a valid key; refuses anything that resolves outside the root."""
        _check(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write next to the target, then rename: a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def url(self, key: str) -> str:
        _check(key)
        return f"/media/{key}"


class R2Storage:
    """Cloudflare R2 through its S3 API; objects are read by visitors from the public bucket URL."""

    def __init__(self, client: Any, bucket: str, public_url: str) -> None:
        self.client = client
        self.bucket = bucket
        self.public_url = public_url.removesuffix("/")

    def put(self, key: str, data: bytes, content_type: str) -> None:
        _check(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl=CACHE_CONTROL,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("storage put failed") from exc

    def delete(self, key: str) -> None:
        _check(key)
        try:
            # S3 answers a delete of a missing object with success, so "missing" is not an error.
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("storage delete failed") from exc

    def url(self, key: str) -> str:
        _check(key)
        return f"{self.public_url}/{key}"


def make_r2_client(settings: Settings) -> Any:
    """An S3 client for the R2 account endpoint. Only call it when `settings.r2_enabled`."""
    assert settings.r2_access_key_id is not None and settings.r2_secret_access_key is not None  # noqa: S101
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint,
        region_name="auto",
        aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.r2_enabled:
        assert settings.r2_bucket is not None and settings.r2_public_url is not None  # noqa: S101
        return R2Storage(make_r2_client(settings), settings.r2_bucket, settings.r2_public_url)
    return LocalDiskStorage(settings.media_root)


def new_key(kind: Literal["a", "p"], *, thumb_of: str | None = None) -> str:
    """A fresh random key; with `thumb_of`, the thumbnail key that belongs to that key."""
    if thumb_of is not None:
        _check(thumb_of)
        if thumb_of.endswith("-t.webp"):
            raise ValueError("a thumbnail has no thumbnail")
        return thumb_of.removesuffix(".webp") + "-t.webp"
    name = secrets.token_hex(16)
    if kind == "a":
        return f"a/{name}.webp"
    now = datetime.now(UTC)
    return f"p/{now:%Y}/{now:%m}/{name}.webp"
