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
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

from app.services.images import MAX_UPLOAD_BYTES

log = logging.getLogger("app.seed.demo")

MANIFEST = Path(__file__).resolve().parent / "data" / "demo_photos.json"
ALLOWED_LICENSES = frozenset({"pd", "cc0"})
# Commons serves originals from upload. and standard-size thumbnails from thumb.
ALLOWED_HOSTS = frozenset({"upload.wikimedia.org", "thumb.wikimedia.org"})
USER_AGENT = "UstoiMan-demo-seed/1.0"
TIMEOUT = 20
RETRIES = 2
RETRY_WAIT = 15.0
SIZE = (1200, 900)

# Takes a URL, returns the file's bytes; raises on any failure. Tests inject a fake one.
Downloader = Callable[[str], bytes]


# --- manifest ---------------------------------------------------------------------------------


def license_ok(entry: dict) -> bool:
    """Only public domain or CC0, from Commons' own file hosts, over https."""
    url = urlsplit(str(entry.get("url", "")))
    return (
        str(entry.get("license_code", "")).lower() in ALLOWED_LICENSES
        and url.scheme == "https"
        and url.hostname in ALLOWED_HOSTS
    )


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


def cached_downloader(
    cache_dir: Path, *, pause: float = 2.0, sleep: Callable[[float], None] = time.sleep
) -> Downloader:
    """Download with a generic User-Agent and a timeout; keep each file in `cache_dir`.

    Commons throttles bursts (HTTP 429), so network fetches are spaced by `pause` seconds and a
    429 is retried twice after waiting; any other failure is left to the caller's fallback.
    """

    def fetch(url: str) -> bytes:
        if not license_ok({"url": url, "license_code": "pd"}):  # host and scheme check only
            raise ValueError("demo photo URL is not on a Commons file host")
        name = hashlib.sha256(url.encode()).hexdigest()
        cached = cache_dir / name
        if cached.is_file():
            return cached.read_bytes()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        for attempt in range(RETRIES + 1):
            sleep(pause)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
                    data = response.read(MAX_UPLOAD_BYTES + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == RETRIES:
                    raise
                sleep(RETRY_WAIT * (attempt + 1))
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("demo photo too large")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    return fetch


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
