"""Where uploaded images live. Keys are generated here and never contain user input.

Local disk is used in development and tests; a Cloudflare R2 implementation can replace it behind
the same `Storage` interface (CLAUDE.md stack).
"""

import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from app.core.config import get_settings

KEY_RE = re.compile(r"^(a|p/\d{4}/\d{2})/[0-9a-f]{32}(-t)?\.webp$")


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


@lru_cache
def get_storage() -> Storage:
    return LocalDiskStorage(get_settings().media_root)


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
