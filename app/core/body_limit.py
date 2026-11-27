"""Cap request bodies before anything parses them.

Starlette spools every multipart file part to a temp file with no total limit, and FastAPI parses
the body before any dependency (sign-in, CSRF, feature flags) runs. Without a cap, an anonymous
client could fill the disk. This pure-ASGI middleware refuses an oversized Content-Length with 413
before the body is read, and counts the bytes of bodies that have no (or a false) length.
"""

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.models.request import MAX_REQUEST_PHOTOS
from app.services.images import MAX_UPLOAD_BYTES

CHECKED_METHODS = {"POST", "PUT", "PATCH"}
DEFAULT_CAP = 64 * 1024
# One image plus multipart framing and the other form fields.
UPLOAD_REQUEST_CAP = MAX_UPLOAD_BYTES + 64 * 1024
# Portfolio forms take several photos at once: the route refuses more than this many files, and
# the body cap is that many full-size files plus framing.
MAX_FILES_PER_UPLOAD = 10
MULTI_UPLOAD_REQUEST_CAP = MAX_FILES_PER_UPLOAD * MAX_UPLOAD_BYTES + 64 * 1024
# A request takes up to MAX_REQUEST_PHOTOS (4) photos: that many full-size files plus framing.
REQUEST_UPLOAD_REQUEST_CAP = MAX_REQUEST_PHOTOS * MAX_UPLOAD_BYTES + 64 * 1024
# Single-image routes (prefixes) and multi-image routes (patterns); extend when routes are added.
UPLOAD_PATH_PREFIXES = ("/master/avatar",)
MULTI_UPLOAD_PATHS = (
    re.compile(r"/master/portfolio/collections/[0-9]+/photos"),
    re.compile(r"/master/onboarding/collection"),
)
REQUEST_UPLOAD_PATHS = (re.compile(r"/requests/[0-9]+/photos"),)


def is_upload_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in UPLOAD_PATH_PREFIXES)


def is_multi_upload_path(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern in MULTI_UPLOAD_PATHS)


def is_request_upload_path(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern in REQUEST_UPLOAD_PATHS)


def request_cap(path: str) -> int:
    # With uploads off the upload routes answer 404, so nobody gets to send them big bodies.
    if not get_settings().uploads_enabled:
        return DEFAULT_CAP
    if is_request_upload_path(path):
        return REQUEST_UPLOAD_REQUEST_CAP
    if is_multi_upload_path(path):
        return MULTI_UPLOAD_REQUEST_CAP
    return UPLOAD_REQUEST_CAP if is_upload_path(path) else DEFAULT_CAP


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in CHECKED_METHODS:
            await self.app(scope, receive, send)
            return
        cap = request_cap(scope["path"])
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    too_big = int(value) > cap
                except ValueError:
                    too_big = True
                if too_big:
                    await _too_large(send)
                    return
        seen = 0
        tripped = False
        started = False

        async def counting_receive() -> Message:
            nonlocal seen, tripped
            if tripped:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    tripped = True
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            if not (tripped and not started):
                raise
        if tripped and not started:
            await _too_large(send)


async def _too_large(send: Send) -> None:
    body = b"Request body too large"
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
