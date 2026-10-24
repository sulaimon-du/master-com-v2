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
    # Behind the proxy, uvicorn replaces the peer address with the forwarded client address.
    return request.client.host if request.client else "unknown"


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    raw = request.cookies.get(cookie_name(SESSION, get_settings()))
    return auth.user_for_session(db, raw)
