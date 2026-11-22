"""Cap request bodies before anything parses them.

Starlette spools every multipart file part to a temp file with no total limit, and FastAPI parses
the body before any dependency (sign-in, CSRF, feature flags) runs. Without a cap, an anonymous
client could fill the disk. This pure-ASGI middleware refuses an oversized Content-Length with 413
before the body is read, and counts the bytes of bodies that have no (or a false) length.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.images import MAX_UPLOAD_BYTES

CHECKED_METHODS = {"POST", "PUT", "PATCH"}
DEFAULT_CAP = 64 * 1024
# One image plus multipart framing and the other form fields. A multi-photo portfolio upload will
# need a larger named cap of its own.
UPLOAD_REQUEST_CAP = MAX_UPLOAD_BYTES + 64 * 1024
# Prefixes of routes that accept files; extend when new upload routes are added.
UPLOAD_PATH_PREFIXES = ("/master/avatar",)


def is_upload_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in UPLOAD_PATH_PREFIXES)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in CHECKED_METHODS:
            await self.app(scope, receive, send)
            return
        cap = UPLOAD_REQUEST_CAP if is_upload_path(scope["path"]) else DEFAULT_CAP
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
