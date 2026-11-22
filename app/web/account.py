"""Account settings: linked sign-in methods, Google linking, sign out everywhere.

Everything here acts on the signed-in user only. There is no URL that takes another user's ID.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from app.core.config import get_settings
from app.core.db import DbSession
from app.models.user import User
from app.services import auth
from app.web.auth import NOTICES, google_redirect, notice_page
from app.web.cookies import SESSION, delete_cookie
from app.web.csrf import CSRFProtected
from app.web.deps import client_ip, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)


@router.get("/account", name="account_page")
def account_page(
    request: Request,
    user: Annotated[User | None, Depends(current_user)],
    notice: str = "",
) -> Response:
    if user is None:
        return RedirectResponse("/signin?next=/account", status_code=303)
    return render(
        request,
        "pages/account.html",
        user=user,
        notice=NOTICES.get(notice),
    )


@router.post("/account/google/link", name="google_link", dependencies=[CSRFProtected])
def google_link(
    request: Request,
    db: DbSession,
    user: Annotated[User | None, Depends(current_user)],
) -> Response:
    settings = get_settings()
    if user is None:
        return RedirectResponse("/signin?next=/account", status_code=303)
    if not settings.google_enabled:
        return notice_page(
            request, user, status=404, title="error.not_found", text="error.not_found"
        )
    if not auth.allow_google_start(db, ip=client_ip(request)):
        return notice_page(
            request, user, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    # POST with CSRF: a link started from another site would attach the attacker's Google account.
    return google_redirect(request, db, user_id=user.id)


@router.post("/account/sessions/revoke", name="revoke_sessions", dependencies=[CSRFProtected])
def revoke_sessions(
    request: Request,
    db: DbSession,
    user: Annotated[User | None, Depends(current_user)],
) -> Response:
    settings = get_settings()
    if user is None:
        return RedirectResponse("/signin?next=/account", status_code=303)
    auth.revoke_all_sessions(db, user.id)
    response = RedirectResponse("/signin?notice=signed_out", status_code=303)
    delete_cookie(response, SESSION, settings)
    return response
