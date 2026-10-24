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
    """The address the rate limits count. Only the proxy's entry is trusted, never the client's.

    Render's proxy appends the real client address to X-Forwarded-For, so the last entry is the
    one to use. The first entry is whatever the client typed. Development has no proxy, so it
    uses the connection address.
    """
    if get_settings().is_production_like:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    raw = request.cookies.get(cookie_name(SESSION, get_settings()))
    return auth.user_for_session(db, raw)
