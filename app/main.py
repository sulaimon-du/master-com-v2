import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import health
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import R2_VARS, Settings, get_settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.web import account, auth, master, media, pages, portfolio, profile, telegram
from app.web.csrf import CSRFError
from app.web.render import render

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # At DEBUG botocore logs request headers, including the signed Authorization header.
    for name in ("botocore", "boto3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    missing = settings.r2_missing
    if 0 < len(missing) < len(R2_VARS):
        log.warning("R2 is partly configured, using local disk; missing: %s", ", ".join(missing))

    app = FastAPI(
        title=settings.app_name,
        # API docs only outside production.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.is_production_like,
        img_origin=settings.r2_public_origin,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    # Added last, so it runs first: the body is capped before anything else sees it.
    app.add_middleware(BodyLimitMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(telegram.router)
    app.include_router(master.router)
    app.include_router(portfolio.router)
    app.include_router(profile.router)
    app.include_router(media.router)
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
