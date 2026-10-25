from fastapi import Request

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.i18n import normalize_lang
from app.models.user import User
from app.services import auth
from app.web.cookies import LANG, SESSION, cookie_name


def current_lang(request: Request) -> str:
    return normalize_lang(request.cookies.get(cookie_name(LANG, get_settings())))


def client_ip(request: Request) -> str:
    """The address the IP rate limits count.

    With `CLIENT_IP_HEADER` set (staging: CF-Connecting-IP, which Cloudflare in front of Render
    overwrites), that header is used. X-Forwarded-For is never read directly: Render appends its
    own proxy address to it. Otherwise the connection address is used; behind uvicorn's
    --proxy-headers with FORWARDED_ALLOW_IPS="*" that is the first X-Forwarded-For entry, which a
    client can forge, so production must set FORWARDED_ALLOW_IPS to its proxy (R-019).
    """
    header = get_settings().client_ip_header
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def current_user(request: Request, db: DbSession) -> User | None:
    raw = request.cookies.get(cookie_name(SESSION, get_settings()))
    return auth.user_for_session(db, raw)
