import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import health
from app.core.config import Settings, get_settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.web import account, auth, pages
from app.web.csrf import CSRFError
from app.web.render import render

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title=settings.app_name,
        # API docs only outside production.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production_like)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.add_exception_handler(CSRFError, csrf_failed)
    return app


def csrf_failed(request: Request, exc: CSRFError) -> HTMLResponse:
    """A form whose token is missing or wrong: say so in words, never a bare 403."""
    return render(
        request,
        "pages/auth/notice.html",
        status=403,
        title_key="error.csrf_title",
        text_key="error.csrf",
        link=None,
    )
