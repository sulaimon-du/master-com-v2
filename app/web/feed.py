"""The masters' pages: the feed, "My responses", a request as a master sees it, respond, withdraw.

Every rule lives in the feed and responses services; a request id a master may not see is a
404 whatever the route (Review Focus 1). `/requests/{id}` itself is dispatched in
`app.web.requests`: the client's view first, then `master_request_page` from here.
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.models.common import PRICE_UNITS
from app.models.user import User
from app.schemas.requests import ResponseInput
from app.services import feed, responses
from app.services.responses import MAX_MESSAGE, ResponseError
from app.web._common import (
    DONE,
    RequestId,
    not_found_page,
    request_url,
    see_other,
    signin_redirect,
)
from app.web.auth import notice_page
from app.web.csrf import CSRFProtected
from app.web.deps import current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]

FEED = "/feed"
MY_RESPONSES = "/responses/mine"
MAX_PRICE_CHARS = 32  # the field allows 16; a forged longer value is simply "not a price"

# A rule broken by the typed values is the form's 422; a state that moved is a 409.
_FORM_CODES = {"message", "price"}


def _err(code: str) -> str:
    return f"feed.err.{code}"


def _done(request: Request) -> str | None:
    done = request.query_params.get("done", "")
    return done if done in DONE else None


# --- the response form ------------------------------------------------------------------------


def _price(raw: str) -> Decimal | None:
    """ "1 000", "1 000" (NBSP) and "1,5" are what people type; anything else is not a price."""
    for space in (" ", "\u00a0", "\u202f"):
        raw = raw.replace(space, "")
    raw = raw.replace(",", ".").strip()
    if not raw:
        return None
    if len(raw) > MAX_PRICE_CHARS:
        raise ResponseError("price")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ResponseError("price") from exc


def response_form(
    message: Annotated[str, Form()] = "",
    price_amount: Annotated[str, Form()] = "",
    price_unit: Annotated[str, Form()] = "",
) -> dict[str, str]:
    # Browsers send CRLF; count LF like the service does. NULs are dropped.
    text = message.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return {"message": text, "price_amount": price_amount, "price_unit": price_unit}


ResponseForm = Annotated[dict[str, str], Depends(response_form)]


def _to_input(values: dict[str, str]) -> ResponseInput:
    try:
        return ResponseInput(
            message=values["message"],
            price_amount=_price(values["price_amount"]),
            price_unit=values["price_unit"].strip() or None,
        )
    except ValidationError as exc:
        raise ResponseError("price") from exc


# --- pages ------------------------------------------------------------------------------------


def master_request_page(
    request: Request,
    db: Session,
    user: User,
    request_id: int,
    *,
    status: int = 200,
    error: str | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse | None:
    """The request as this master may see it, or None when they may not (the caller 404s)."""
    view = responses.request_for_master(db, user, request_id, lang=current_lang(request))
    if view is None:
        return None
    if view.status == "unavailable":
        # "No longer available": no description, photos or place (spec D10.9).
        return render(
            request,
            "pages/requests/unavailable.html",
            user=user,
            status=status,
            view=view,
            error=error,
        )
    return render(
        request,
        "pages/requests/detail_master.html",
        user=user,
        status=status,
        view=view,
        error=error,
        values=values or {"message": "", "price_amount": "", "price_unit": ""},
        done=_done(request),
        units=PRICE_UNITS,
        message_max=MAX_MESSAGE,
    )


@router.get(FEED, name="feed")
def feed_page(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    after: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    if user is None:
        return signin_redirect(FEED)
    page = feed.feed(db, user, after, lang=current_lang(request))
    return render(request, "pages/feed.html", user=user, page=page, first=after is None)


@router.get(MY_RESPONSES, name="responses_mine")
def responses_mine(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return signin_redirect(MY_RESPONSES)
    cards = responses.my_responses(db, user, lang=current_lang(request))
    return render(
        request, "pages/requests/my_responses.html", user=user, cards=cards, done=_done(request)
    )


def _stale(
    request: Request,
    db: Session,
    user: User,
    request_id: int,
    code: str,
    values: dict[str, str] | None = None,
) -> Response:
    """A friendly 409/422 on the request page, or on a bare notice when the page is gone."""
    status = 422 if code in _FORM_CODES else 409
    page = master_request_page(
        request, db, user, request_id, status=status, error=_err(code), values=values
    )
    if page is not None:
        return page
    return notice_page(request, user, status=status, title="feed.err.title", text=_err(code))


@router.post("/requests/{request_id}/respond", name="request_respond", dependencies=[CSRFProtected])
def request_respond(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    request_id: RequestId,
    values: ResponseForm,
) -> Response:
    if user is None:
        return signin_redirect(request_url(request_id))
    try:
        responses.respond(db, user, request_id, _to_input(values))
    except ResponseError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return not_found_page(request, user)
        kept = values if exc.code in _FORM_CODES else None
        return _stale(request, db, user, request_id, exc.code, values=kept)
    return see_other(request_url(request_id, "?done=responded"))


@router.post(
    "/requests/{request_id}/withdraw", name="request_withdraw", dependencies=[CSRFProtected]
)
def request_withdraw(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId
) -> Response:
    if user is None:
        return signin_redirect(request_url(request_id))
    try:
        responses.withdraw(db, user, request_id)
    except ResponseError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return not_found_page(request, user)
        return _stale(request, db, user, request_id, exc.code)
    return see_other(f"{MY_RESPONSES}?done=withdrawn")
