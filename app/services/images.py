"""Upload image pipeline: validate, strip metadata, re-encode to WebP.

Pure functions: no database, storage or settings. Callers map ``ImageError.code``
to user-facing text; the exception messages never contain user data.
"""

import io
import warnings
from dataclasses import dataclass
from typing import BinaryIO, Literal

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
PORTFOLIO_LONG_SIDE = 1600
THUMB_LONG_SIDE = 400
AVATAR_SIZE = 400
WEBP_QUALITY = 80

# Pillow's own guard is a second line of defence behind our explicit check.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

ErrorCode = Literal["type", "size", "pixels", "unreadable"]

_CHUNK = 64 * 1024


class ImageError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code)
        self.code: ErrorCode = code


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    width: int
    height: int


def read_limited(stream: BinaryIO) -> bytes:
    """Read at most MAX_UPLOAD_BYTES; fail as soon as the stream exceeds it."""
    buf = bytearray()
    while chunk := stream.read(_CHUNK):
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            raise ImageError("size")
    return bytes(buf)


def _sniff(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _to_rgb(img: Image.Image) -> Image.Image:
    """Flatten any mode (alpha, palette, 16-bit, CMYK) to RGB on a white ground."""
    if img.mode in ("I;16", "I;16L", "I;16B", "I"):
        img = img.point(lambda v: v / 256).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def _encode(img: Image.Image) -> ProcessedImage:
    buf = io.BytesIO()
    # No exif/icc_profile/xmp passed, so none is written.
    img.save(buf, "WEBP", quality=WEBP_QUALITY)
    return ProcessedImage(buf.getvalue(), img.width, img.height)


def _decode(data: bytes) -> Image.Image:
    """Validate and decode to an upright RGB image with no metadata."""
    if not _sniff(data):
        raise ImageError("type")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.width * img.height > MAX_PIXELS:
                raise ImageError("pixels")
            img.load()
            img = ImageOps.exif_transpose(img)
            return _to_rgb(img)
    except ImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError("pixels") from None
    except Exception:
        raise ImageError("unreadable") from None


def _guard(fn, data: bytes):
    try:
        return fn(data)
    except ImageError:
        raise
    except Exception:
        raise ImageError("unreadable") from None


def _portfolio(data: bytes) -> tuple[ProcessedImage, ProcessedImage]:
    img = _decode(data)
    full = img.copy()
    full.thumbnail((PORTFOLIO_LONG_SIDE, PORTFOLIO_LONG_SIDE), Image.Resampling.LANCZOS)
    thumb = img.copy()
    thumb.thumbnail((THUMB_LONG_SIDE, THUMB_LONG_SIDE), Image.Resampling.LANCZOS)
    return _encode(full), _encode(thumb)


def _avatar(data: bytes) -> ProcessedImage:
    img = _decode(data)
    square = ImageOps.fit(
        img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )
    return _encode(square)


def process_portfolio(data: bytes) -> tuple[ProcessedImage, ProcessedImage]:
    """Return (full, thumbnail): long side 1600 / 400, never upscaled."""
    return _guard(_portfolio, data)


def process_avatar(data: bytes) -> ProcessedImage:
    """Return a 400x400 centre-cropped WebP."""
    return _guard(_avatar, data)
