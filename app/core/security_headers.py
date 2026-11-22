"""Security headers for every response. Pages load scripts and styles only from our own origin."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        # "Link Google" is a POST form answered by a redirect to Google; browsers check
        # form-action against that redirect too (R-019).
        "form-action 'self' https://accounts.google.com",
        "frame-ancestors 'none'",
    ]
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if self.hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
