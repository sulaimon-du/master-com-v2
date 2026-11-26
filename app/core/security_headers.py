"""Security headers for every response. Pages load scripts and styles only from our own origin."""

from functools import lru_cache

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


@lru_cache
def build_csp(img_origin: str | None) -> str:
    """The policy; `img_origin` is the R2 public bucket origin (scheme + host) when R2 is on."""
    img_src = "img-src 'self' data: blob:" + (f" {img_origin}" if img_origin else "")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            img_src,
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            # "Link Google" and "Sign in with Telegram" are POST forms answered by a redirect to
            # Google or Telegram; browsers check form-action against that redirect too (R-019).
            "form-action 'self' https://accounts.google.com https://t.me",
            "frame-ancestors 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(  # type: ignore[no-untyped-def]
        self, app, *, hsts: bool, img_origin: str | None = None, noindex: bool = False
    ) -> None:
        super().__init__(app)
        self.hsts = hsts
        self.noindex = noindex  # staging, development, test: keep every response out of search
        self.csp = build_csp(img_origin)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", self.csp)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if self.noindex:
            headers["X-Robots-Tag"] = "noindex, nofollow"
        if self.hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
