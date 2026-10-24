from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.i18n import normalize_lang
from app.models.user import User
from app.services import auth
from app.web.cookies import LANG, SESSION, cookie_name


def current_lang(request: Request) -> str:
    return normalize_lang(request.cookies.get(cookie_name(LANG, get_settings())))


def client_ip(request: Request) -> str:
    """The address the IP rate limits count.

    Staging and production: Render serves traffic through Cloudflare, which sets CF-Connecting-IP
    to the visitor's address. X-Forwarded-For is not used, because Render appends its own proxy
    address to it, so its last entry may be shared by every visitor. Both points come from Render
    community reports, not official docs; R-019 lists the black-box check to run on staging.
    Without the header, the connection address is used. Behind uvicorn's --proxy-headers that is
    the first X-Forwarded-For entry, which a client can forge; the per-email limits still hold.
    """
    if get_settings().is_production_like:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    raw = request.cookies.get(cookie_name(SESSION, get_settings()))
    return auth.user_for_session(db, raw)
