"""Images for the demo accounts (R-027): real PD/CC0 work photos, generated fallbacks, avatars.

Portfolio photos come from `data/demo_photos.json`, a manifest of Wikimedia Commons files whose
licence is public domain or CC0 (credits: `docs/demo_photo_credits.md`). Each file is downloaded
once into a git-ignored cache and then goes through the normal upload pipeline like any upload.
When a download fails, or no manifest photo fits a topic, a Pillow-drawn picture is used instead.
Avatars are always generated initials: fake accounts never get a real person's face.
"""

import hashlib
import io
import json
import logging
import os
import random
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

from app.services.images import MAX_UPLOAD_BYTES

log = logging.getLogger("app.seed.demo")

MANIFEST = Path(__file__).resolve().parent / "data" / "demo_photos.json"
# The licence code and the recorded licence text must agree (Commons' LicenseShortName).
LICENSES = {"pd": "Public domain", "cc0": "CC0"}
# Commons serves originals from upload. and standard-size thumbnails from thumb.
ALLOWED_HOSTS = frozenset({"upload.wikimedia.org", "thumb.wikimedia.org"})
USER_AGENT = "UstoiMan-demo-seed/1.0"
TIMEOUT = 20
RETRIES = 2
RETRY_WAIT = 15.0
SIZE = (1200, 900)

# Takes a URL, returns the file's bytes; raises on any failure. Tests inject a fake one.
# A downloader may also have `forget(url)`, called when the pipeline refuses the bytes.
Downloader = Callable[[str], bytes]
# Opens a request with a timeout; returns a response (`read`, `geturl`, context manager).
Opener = Callable[[urllib.request.Request, float], Any]


# --- manifest ---------------------------------------------------------------------------------


def url_ok(url: str) -> bool:
    """https on one of Commons' own file hosts, nothing else."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS


def license_ok(entry: dict) -> bool:
    """Public domain or CC0 only (code and recorded text agree), from a Commons file host."""
    code = str(entry.get("license_code", ""))
    return LICENSES.get(code) == entry.get("license") and url_ok(str(entry.get("url", "")))


def load_manifest(path: Path = MANIFEST) -> dict[str, list[dict]]:
    """Manifest entries by topic (specialty slug); anything not PD/CC0 is dropped here too."""
    entries = json.loads(path.read_text(encoding="utf-8"))["photos"]
    by_topic: dict[str, list[dict]] = {}
    for entry in entries:
        if license_ok(entry):
            by_topic.setdefault(entry["specialty"], []).append(entry)
        else:
            log.warning("demo photo skipped, licence not PD/CC0: %s", entry.get("title"))
    return by_topic


# --- download ---------------------------------------------------------------------------------


class _CommonsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to https on a Commons file host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not url_ok(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect off Commons refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request, timeout: float) -> Any:
    opener = urllib.request.build_opener(_CommonsOnlyRedirects())
    return opener.open(request, timeout=timeout)


class CachedDownloader:
    """Download with a generic User-Agent and a timeout; keep each file in `cache_dir`.

    Commons throttles bursts (HTTP 429), so network fetches are spaced by `pause` seconds and a
    429 is retried twice after waiting; any other failure is left to the caller's fallback.
    Redirects are followed only to Commons file hosts, and the final URL is checked again.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        pause: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        opener: Opener = _open,
    ) -> None:
        self.cache_dir, self.pause, self.sleep, self.opener = cache_dir, pause, sleep, opener

    def path(self, url: str) -> Path:
        return self.cache_dir / hashlib.sha256(url.encode()).hexdigest()

    def forget(self, url: str) -> None:
        self.path(url).unlink(missing_ok=True)

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        for attempt in range(RETRIES + 1):
            self.sleep(self.pause)
            try:
                with self.opener(request, TIMEOUT) as response:
                    if not url_ok(response.geturl()):
                        raise ValueError("demo photo was served from outside Commons")
                    return response.read(MAX_UPLOAD_BYTES + 1)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == RETRIES:
                    raise
                self.sleep(RETRY_WAIT * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def __call__(self, url: str) -> bytes:
        if not url_ok(url):
            raise ValueError("demo photo URL is not on a Commons file host")
        cached = self.path(url)
        if cached.is_file():
            return cached.read_bytes()
        data = self._download(url)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("demo photo too large")
        # Write next to the target, then rename: a crash never leaves half a file in the cache.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, cached)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return data


def cached_downloader(cache_dir: Path, **kwargs: Any) -> CachedDownloader:
    return CachedDownloader(cache_dir, **kwargs)


# --- generated pictures -----------------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _tiles(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    base = rnd.choice([(222, 214, 200), (200, 214, 222), (230, 230, 228)])
    step = rnd.choice([100, 120, 150])
    for x in range(0, SIZE[0], step):
        for y in range(0, SIZE[1], step):
            shade = rnd.randint(-12, 12)
            d.rectangle(
                (x + 3, y + 3, x + step - 3, y + step - 3), fill=tuple(c + shade for c in base)
            )


def _bricks(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    d.rectangle((0, 0, *SIZE), fill=(190, 185, 175))
    h, w = 60, 180
    for row, y in enumerate(range(0, SIZE[1], h)):
        offset = (w // 2) * (row % 2)
        for x in range(-offset, SIZE[0], w):
            red = rnd.randint(140, 175)
            d.rectangle((x + 4, y + 4, x + w - 4, y + h - 4), fill=(red, red // 2 + 20, 50))


def _gradient(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    top = (rnd.randint(200, 245), rnd.randint(200, 245), rnd.randint(190, 235))
    bottom = tuple(max(c - 60, 0) for c in top)
    for y in range(SIZE[1]):
        t = y / SIZE[1]
        d.line(
            (0, y, SIZE[0], y),
            fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)),
        )


def _pipes(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    d.rectangle((0, 0, *SIZE), fill=(235, 235, 232))
    for i in range(5):
        y = 150 + i * 140
        colour = rnd.choice([(184, 115, 51), (160, 160, 165), (90, 120, 200)])
        d.rectangle((80, y, SIZE[0] - 80, y + 36), fill=colour)
        d.rectangle((300 + i * 80, y - 20, 340 + i * 80, y + 56), fill=(120, 120, 125))


def _panel(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    d.rectangle((0, 0, *SIZE), fill=(210, 212, 215))
    d.rectangle((250, 120, 950, 780), fill=(245, 245, 245), outline=(80, 80, 80), width=6)
    for row in range(3):
        for col in range(8):
            x, y = 300 + col * 80, 180 + row * 190
            d.rectangle((x, y, x + 56, y + 130), fill=(40, 40, 45))
            d.rectangle(
                (x + 18, y + 30, x + 38, y + 70), fill=rnd.choice([(220, 60, 60), (230, 230, 230)])
            )


def _planks(d: ImageDraw.ImageDraw, rnd: random.Random) -> None:
    for i, y in enumerate(range(0, SIZE[1], 90)):
        tone = rnd.randint(150, 190)
        d.rectangle((0, y, SIZE[0], y + 88), fill=(tone, tone - 50, tone - 100))
        seam = rnd.randint(200, 1000)
        d.line((seam, y, seam, y + 88), fill=(90, 60, 30), width=3)
        d.line((0, y + 88, SIZE[0], y + 88), fill=(80, 50, 25), width=2 + i % 2)


PATTERNS = {
    "plitochnik": _tiles,
    "kamenshchik": _bricks,
    "krovelshchik": _bricks,
    "santekhnik": _pipes,
    "elektrik": _panel,
    "plotnik": _planks,
    "polovshchik": _planks,
    "kukhni": _planks,
}


def generated_photo(topic: str, seed: int) -> bytes:
    """An illustrative 1200x900 'work' picture for a topic, labelled DEMO, as PNG bytes."""
    rnd = random.Random(f"{topic}:{seed}")  # noqa: S311 - pictures, not secrets
    img = Image.new("RGB", SIZE, (230, 230, 230))
    d = ImageDraw.Draw(img)
    PATTERNS.get(topic, _gradient)(d, rnd)
    d.rectangle((SIZE[0] - 170, SIZE[1] - 70, SIZE[0] - 10, SIZE[1] - 10), fill=(20, 20, 20))
    d.text((SIZE[0] - 155, SIZE[1] - 64), "DEMO", fill=(255, 255, 255), font=_font(40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


AVATAR_COLOURS = [(37, 99, 235), (5, 150, 105), (217, 119, 6), (190, 24, 93), (79, 70, 229)]


def avatar(initials: str, seed: int) -> bytes:
    """A coloured square with the initials (Latin letters: the bundled font has no Cyrillic)."""
    img = Image.new("RGB", (400, 400), AVATAR_COLOURS[seed % len(AVATAR_COLOURS)])
    d = ImageDraw.Draw(img)
    d.text((200, 200), initials, fill=(255, 255, 255), font=_font(170), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
