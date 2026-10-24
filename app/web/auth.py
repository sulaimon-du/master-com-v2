"""Sign-in, registration, email confirmation, password reset and Google sign-in.

Routes parse input, call a service and render. Rules live in app/services/auth.py.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.i18n import LANGS, normalize_lang
from app.core.security import new_token, tokens_match
from app.models.user import User
from app.schemas.auth import EmailForm, NewPasswordForm, RegisterForm, SignInForm
from app.services import auth, google_oidc
from app.services.email import EmailSender, get_email_sender
from app.web.cookies import (
    CSRF,
    CSRF_MAX_AGE,
    LANG,
    OAUTH,
    OAUTH_MAX_AGE,
    SESSION,
    cookie_name,
    delete_cookie,
    set_cookie,
)
from app.web.csrf import CSRFProtected
from app.web.deps import client_ip, current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)
log = logging.getLogger(__name__)

SESSION_MAX_AGE = int(auth.SESSION_ABSOLUTE.total_seconds())
# Notices are keys from a fixed list, never free text from the URL.
NOTICES = {
    "linked": "account.linked_ok",
    "already": "account.linked_already",
    "taken": "account.linked_taken",
    "failed": "account.linked_failed",
    "signed_out": "account.signed_out",
}
LANG_MAX_AGE = 60 * 60 * 24 * 365


def safe_next(value: str | None) -> str:
    """Only same-site paths. Anything else goes to the account page (no open redirect)."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/account"


def url_builder(request: Request) -> auth.UrlFor:
    def build(name: str, params: dict[str, str]) -> str:
        return str(request.url_for(name).include_query_params(**params))

    return build


def form_errors(error: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "form"
        if field == "password" and item["type"] == "string_too_short" and item.get("input") == "":
            errors[field] = "validation.password_required"
        else:
            errors[field] = f"validation.{field}"
    return errors


def notice_page(
    request: Request,
    user: User | None,
    *,
    title: str,
    text: str,
    status: int = 200,
    link: str | None = None,
) -> HTMLResponse:
    return render(
        request,
        "pages/auth/notice.html",
        user=user,
        status=status,
        title_key=title,
        text_key=text,
        link=link,
    )


def signin_page_response(
    request: Request,
    *,
    email: str = "",
    next_url: str = "/account",
    error: str | None = None,
    show_resend: bool = False,
    status: int = 200,
    errors: dict[str, str] | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    return render(
        request,
        "pages/auth/signin.html",
        status=status,
        email=email,
        next_url=next_url,
        error=error,
        notice=notice,
        show_resend=show_resend,
        errors=errors or {},
    )


def _attach_session(response: Response, raw: str, settings: Settings) -> None:
    set_cookie(response, SESSION, raw, settings, max_age=SESSION_MAX_AGE)
    # A new CSRF token at sign-in too: the pre-login form is no longer trusted.
    set_cookie(response, CSRF, new_token(), settings, max_age=CSRF_MAX_AGE)


# --- Sign-in and sign-out ----------------------------------------------------------------


@router.get("/signin", name="signin_page")
def signin_page(request: Request, next: str | None = None, notice: str = "") -> HTMLResponse:
    return signin_page_response(request, next_url=safe_next(next), notice=NOTICES.get(notice))


@router.post("/signin", name="signin_submit", dependencies=[CSRFProtected])
def signin_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/account",
) -> Response:
    next_url = safe_next(next)
    try:
        form = SignInForm(email=email, password=password)
    except ValidationError as exc:
        return signin_page_response(
            request, email=email, next_url=next_url, status=422, errors=form_errors(exc)
        )

    outcome, user = auth.sign_in_with_password(
        db, ip=client_ip(request), email=form.email, password=form.password
    )
    if outcome is auth.Outcome.RATE_LIMITED:
        return signin_page_response(
            request,
            email=form.email,
            next_url=next_url,
            error="error.rate_limited",
            status=429,
        )
    if outcome is auth.Outcome.UNCONFIRMED:
        return signin_page_response(
            request,
            email=form.email,
            next_url=next_url,
            error="signin.unconfirmed",
            show_resend=True,
            status=403,
        )
    if outcome is not auth.Outcome.OK or user is None:
        return signin_page_response(
            request, email=form.email, next_url=next_url, error="signin.wrong", status=401
        )

    settings = get_settings()
    raw = auth.open_session(db, user, replace=request.cookies.get(cookie_name(SESSION, settings)))
    response = RedirectResponse(next_url, status_code=303)
    _attach_session(response, raw, settings)
    return response


@router.post("/signout", name="signout", dependencies=[CSRFProtected])
def signout(request: Request, db: Annotated[Session, Depends(get_db)]) -> RedirectResponse:
    settings = get_settings()
    auth.end_session(db, request.cookies.get(cookie_name(SESSION, settings)))
    response = RedirectResponse("/signin?notice=signed_out", status_code=303)
    delete_cookie(response, SESSION, settings)
    return response


# --- Registration and confirmation --------------------------------------------------------


@router.get("/register", name="register_page")
def register_page(request: Request) -> HTMLResponse:
    return render(request, "pages/auth/register.html", errors={}, form={})


@router.post("/register", name="register_submit", dependencies=[CSRFProtected])
def register_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        form = RegisterForm(name=name, email=email, password=password)
    except ValidationError as exc:
        return render(
            request,
            "pages/auth/register.html",
            status=422,
            errors=form_errors(exc),
            form={"name": name, "email": email},
        )
    lang = current_lang(request)
    outcome = auth.register(
        db,
        sender,
        ip=client_ip(request),
        name=form.name,
        email=form.email,
        password=form.password,
        lang=lang,
        url_for=url_builder(request),
    )
    if outcome is auth.Outcome.RATE_LIMITED:
        return notice_page(
            request,
            None,
            status=429,
            title="error.rate_limited_title",
            text="error.rate_limited",
        )
    # Same page whether or not the address was already registered.
    return notice_page(
        request,
        None,
        title="register.sent_title",
        text="register.sent_text",
        link=request.url_for("signin_page").path,
    )


@router.get("/confirm", name="confirm_page")
def confirm_page(request: Request, token: str = "") -> HTMLResponse:
    return render(request, "pages/auth/confirm.html", token=token)


@router.post("/confirm", name="confirm_submit", dependencies=[CSRFProtected])
def confirm_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Form()] = "",
) -> HTMLResponse:
    if not token or auth.confirm_email(db, token) is not auth.Outcome.OK:
        return render(
            request,
            "pages/auth/confirm.html",
            status=400,
            token="",
            error="confirm.invalid",
        )
    return notice_page(
        request,
        None,
        title="confirm.done_title",
        text="confirm.done_text",
        link=request.url_for("signin_page").path,
    )


@router.get("/confirm/resend", name="resend_page")
def resend_page(request: Request) -> HTMLResponse:
    return render(request, "pages/auth/email_form.html", mode="resend", email="", errors={})


@router.post("/confirm/resend", name="resend_submit", dependencies=[CSRFProtected])
def resend_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        form = EmailForm(email=email)
    except ValidationError:
        return render(
            request,
            "pages/auth/email_form.html",
            mode="resend",
            status=422,
            email=email,
            errors={"email": "validation.email"},
        )
    outcome = auth.resend_confirmation(
        db,
        sender,
        ip=client_ip(request),
        email=form.email,
        lang=current_lang(request),
        url_for=url_builder(request),
    )
    if outcome is auth.Outcome.RATE_LIMITED:
        return notice_page(
            request, None, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    return notice_page(request, None, title="confirm.resend_title", text="confirm.resend_text")


# --- Password reset -----------------------------------------------------------------------


@router.get("/password/forgot", name="forgot_page")
def forgot_page(request: Request) -> HTMLResponse:
    return render(request, "pages/auth/email_form.html", mode="forgot", email="", errors={})


@router.post("/password/forgot", name="forgot_submit", dependencies=[CSRFProtected])
def forgot_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        form = EmailForm(email=email)
    except ValidationError:
        return render(
            request,
            "pages/auth/email_form.html",
            mode="forgot",
            status=422,
            email=email,
            errors={"email": "validation.email"},
        )
    outcome = auth.request_password_reset(
        db,
        sender,
        ip=client_ip(request),
        email=form.email,
        lang=current_lang(request),
        url_for=url_builder(request),
    )
    if outcome is auth.Outcome.RATE_LIMITED:
        return notice_page(
            request, None, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    return notice_page(request, None, title="forgot.sent_title", text="forgot.sent_text")


@router.get("/password/reset", name="reset_page")
def reset_page(request: Request, token: str = "") -> HTMLResponse:
    return render(request, "pages/auth/reset.html", token=token, errors={})


@router.post("/password/reset", name="reset_submit", dependencies=[CSRFProtected])
def reset_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        form = NewPasswordForm(token=token, password=password)
    except ValidationError as exc:
        return render(
            request, "pages/auth/reset.html", status=422, token=token, errors=form_errors(exc)
        )
    outcome = auth.reset_password(db, ip=client_ip(request), raw=form.token, password=form.password)
    if outcome is auth.Outcome.RATE_LIMITED:
        return notice_page(
            request, None, status=429, title="error.rate_limited_title", text="error.rate_limited"
        )
    if outcome is not auth.Outcome.OK:
        return render(
            request, "pages/auth/reset.html", status=400, token="", errors={}, error="reset.invalid"
        )
    return notice_page(
        request,
        None,
        title="reset.done_title",
        text="reset.done_text",
        link=request.url_for("signin_page").path,
    )


# --- Google ------------------------------------------------------------------------------


def _google_redirect(request: Request, db: Session, user_id: int | None) -> RedirectResponse:
    settings = get_settings()
    state, url = google_oidc.begin_flow(
        db,
        redirect_uri=str(request.url_for("google_callback")),
        client_id=settings.google_client_id or "",
        user_id=user_id,
    )
    response = RedirectResponse(url, status_code=303)
    set_cookie(response, OAUTH, state, settings, max_age=OAUTH_MAX_AGE)
    return response


@router.get("/auth/google/start", name="google_start")
def google_start(request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    if not get_settings().google_enabled:
        return notice_page(
            request, None, status=404, title="error.not_found", text="error.not_found"
        )
    return _google_redirect(request, db, user_id=None)


def _google_fail(request: Request, user: User | None, status: int = 400) -> HTMLResponse:
    response = notice_page(
        request, user, status=status, title="google.failed", text="google.failed"
    )
    delete_cookie(response, OAUTH, get_settings())
    return response


@router.get("/auth/google/callback", name="google_callback")
def google_callback(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(current_user)],
    state: Annotated[str, Query()] = "",
    code: Annotated[str, Query()] = "",
    error: Annotated[str, Query()] = "",
) -> Response:
    settings = get_settings()
    if not settings.google_enabled:
        return notice_page(
            request, user, status=404, title="error.not_found", text="error.not_found"
        )
    bound = request.cookies.get(cookie_name(OAUTH, settings))
    if error or not code or not state or not bound or not tokens_match(bound, state):
        return _google_fail(request, user)
    flow = google_oidc.take_flow(db, state)
    if flow is None:
        return _google_fail(request, user)
    try:
        id_token = google_oidc.exchange_code(
            code=code,
            code_verifier=flow.code_verifier,
            client_id=settings.google_client_id or "",
            client_secret=settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else "",
            redirect_uri=str(request.url_for("google_callback")),
        )
        identity = google_oidc.verify_id_token(
            id_token, client_id=settings.google_client_id or "", nonce=flow.nonce
        )
    except google_oidc.GoogleError:
        log.info("google sign-in rejected")
        return _google_fail(request, user)

    if flow.user_id is not None:
        # Linking from settings: the signed-in user must be the one who started the flow.
        if user is None or user.id != flow.user_id:
            return _google_fail(request, user, status=403)
        outcome = auth.link_google(db, user, identity)
        notice = {
            auth.Outcome.OK: "linked",
            auth.Outcome.GOOGLE_ALREADY_LINKED: "already",
            auth.Outcome.GOOGLE_TAKEN: "taken",
        }.get(outcome, "failed")
        response = RedirectResponse(f"/account?notice={notice}", status_code=303)
        delete_cookie(response, OAUTH, settings)
        if outcome is auth.Outcome.OK:
            raw = auth.open_session(
                db, user, replace=request.cookies.get(cookie_name(SESSION, settings))
            )
            _attach_session(response, raw, settings)
        return response

    outcome, account = auth.sign_in_with_google(db, identity, lang=current_lang(request))
    if outcome is auth.Outcome.EMAIL_TAKEN:
        response = signin_page_response(
            request, next_url="/account", error="signin.google_taken", status=409
        )
    elif outcome is not auth.Outcome.OK or account is None:
        response = notice_page(
            request, None, status=403, title="google.failed", text="google.blocked"
        )
    else:
        raw = auth.open_session(
            db, account, replace=request.cookies.get(cookie_name(SESSION, settings))
        )
        response = RedirectResponse("/account", status_code=303)
        _attach_session(response, raw, settings)
    delete_cookie(response, OAUTH, settings)
    return response


# --- Language -----------------------------------------------------------------------------


@router.get("/lang/{code}", name="set_lang")
def set_lang(code: str) -> RedirectResponse:
    if code not in LANGS:
        code = normalize_lang(None)
    response = RedirectResponse("/", status_code=303)
    set_cookie(response, LANG, code, get_settings(), max_age=LANG_MAX_AGE)
    return response
