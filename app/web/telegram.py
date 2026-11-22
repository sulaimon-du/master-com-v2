"""Telegram sign-in page and link start. Rules live in app/services/telegram.py.

The one-time token sits in the path, so it is never logged here. Opening the page does not use
it (messengers and scanners preview links); only the POST does.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.core.db import DbSession
from app.models.user import User
from app.services import auth, telegram
from app.web.auth import _attach_session, notice_page
from app.web.cookies import SESSION, cookie_name
from app.web.csrf import CSRFProtected
from app.web.deps import client_ip, current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)


def _not_found(request: Request, user: User | None = None) -> HTMLResponse:
    return notice_page(request, user, status=404, title="error.not_found", text="error.not_found")


def _bad_link(request: Request) -> HTMLResponse:
    return notice_page(
        request,
        None,
        status=400,
        title="telegram.signin_title",
        text="telegram.link_bad",
        link=request.url_for("signin_page").path,
    )


@router.get("/auth/telegram/{token}", name="telegram_signin_page")
def telegram_signin_page(request: Request, db: DbSession, token: str) -> Response:
    if not get_settings().telegram_enabled:
        return _not_found(request)
    preview = telegram.signin_preview(db, token)
    if preview is None:
        return _bad_link(request)
    return render(request, "pages/telegram_signin.html", token=token, first_name=preview.first_name)


@router.post("/auth/telegram/{token}", name="telegram_signin_submit", dependencies=[CSRFProtected])
def telegram_signin_submit(request: Request, db: DbSession, token: str) -> Response:
    settings = get_settings()
    if not settings.telegram_enabled:
        return _not_found(request)
    outcome, user = telegram.sign_in_with_telegram(
        db, ip=client_ip(request), raw=token, lang=current_lang(request)
    )
    if outcome is auth.Outcome.RATE_LIMITED:
        return notice_page(
            request, None, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    if outcome is not auth.Outcome.OK or user is None:
        return _bad_link(request)
    raw = auth.open_session(db, user, replace=request.cookies.get(cookie_name(SESSION, settings)))
    response = RedirectResponse("/account", status_code=303)
    _attach_session(response, raw, settings)
    return response


@router.post("/account/telegram/link", name="telegram_link", dependencies=[CSRFProtected])
def telegram_link(
    request: Request,
    db: DbSession,
    user: Annotated[User | None, Depends(current_user)],
) -> Response:
    settings = get_settings()
    if user is None:
        return RedirectResponse("/signin?next=/account", status_code=303)
    if not settings.telegram_enabled:
        return _not_found(request, user)
    if not telegram.allow_link_start(db, user_id=user.id, ip=client_ip(request)):
        return notice_page(
            request, user, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    raw = telegram.issue_link(db, user)
    url = f"https://t.me/{settings.telegram_bot_username}?start=link_{raw}"
    response = RedirectResponse(url, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response
