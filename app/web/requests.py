"""The client's request pages: post, list, open, edit, photos, close, cancel, reopen.

Every request id from a URL is resolved through the signed-in client by the service, so someone
else's request is "not found" (404) whatever the route. Masters get their own views in a later
task; today only the client opens `/requests/{id}`.
"""

import logging
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request
from fastapi.datastructures import UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import DbSession
from app.models.request import MAX_REQUEST_PHOTOS
from app.models.user import User
from app.schemas.requests import FormValues, RequestInput, RequestView
from app.services import request_options, requests
from app.services.images import ImageError, read_limited
from app.services.requests import DESCRIPTION_RANGE, MAX_ADDRESS, RequestCode, RequestError
from app.services.storage import Storage, StorageError, get_storage
from app.web.auth import notice_page
from app.web.csrf import CSRFProtected
from app.web.deps import current_lang, current_user
from app.web.render import render
from app.web.templating import REQUEST_DONE_CODES

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]
Uploads = Annotated[list[UploadFile] | None, File()]

# Banners after a redirect; anything else in the query is ignored.
DONE = set(REQUEST_DONE_CODES)

NEW = "/requests/new"
MINE = "/requests/mine"
MAX_FORM_SPECIALTIES = 20  # more than the 3 the service accepts; keeps a forged form cheap
MAX_ID_DIGITS = 9  # ids are 32-bit; longer digit strings can never match a row
FileError = tuple[int, str]
# 32-bit ids: a bigger number can never be a row, and the database would refuse it.
RequestId = Annotated[int, Path(ge=1, le=2_147_483_647)]
PhotoId = Annotated[int, Path(ge=1, le=2_147_483_647)]


def _url(request_id: int, query: str = "") -> str:
    return f"/requests/{request_id}{query}"


def _signin(path: str) -> RedirectResponse:
    # The path may carry a query (the prefill); encode it so it stays inside `next`.
    return RedirectResponse(f"/signin?next={quote(path, safe='/')}", status_code=303)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _not_found(request: Request, user: User | None) -> HTMLResponse:
    return notice_page(request, user, status=404, title="error.not_found", text="error.not_found")


def _err(code: str) -> str:
    return f"requests.err.{code}"


# --- the form: strings in, a validated RequestInput out ---------------------------------------


def _text(raw: str) -> str:
    """Free text from a textarea: browsers send CRLF; store and count LF. NULs are dropped."""
    return raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _budget(raw: str) -> str | None:
    """ "1 000", "1 000" (NBSP / narrow NBSP) and "1,5" are what people type."""
    for space in (" ", "\u00a0", "\u202f"):
        raw = raw.replace(space, "")
    return raw.replace(",", ".").strip() or None


def request_form(
    specialty: Annotated[list[str], Form()] = [],  # noqa: B006  (FastAPI reads the default)
    city: Annotated[str, Form()] = "",
    district: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    budget_amount: Annotated[str, Form()] = "",
    budget_unit: Annotated[str, Form()] = "",
    timing: Annotated[str, Form()] = "flexible",
    timing_date: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
) -> FormValues:
    return FormValues(
        specialty=specialty[:MAX_FORM_SPECIALTIES],
        city=city,
        district=district,
        description=_text(description),
        budget_amount=budget_amount,
        budget_unit=budget_unit,
        timing=timing,
        timing_date=timing_date,
        address=_text(address),
    )


FormData = Annotated[FormValues, Depends(request_form)]


def _id(raw: str, code: RequestCode) -> int:
    raw = raw.strip()
    if not (raw.isascii() and raw.isdigit() and len(raw) <= MAX_ID_DIGITS):
        raise RequestError(code)
    return int(raw)


# Which service code a pydantic complaint about a field means.
_FIELD_CODE: dict[str, RequestCode] = {
    "budget_amount": "budget",
    "budget_unit": "budget",
    "timing": "timing",
    "timing_date": "timing",
}


def _to_input(values: FormValues) -> RequestInput:
    """Parse the typed strings; anything unparseable is a RequestError, never a 500."""
    specialty_ids = [_id(s, "specialty") for s in values.specialty]
    city_id = _id(values.city, "place")
    district_id = _id(values.district, "place") if values.district.strip() else None
    try:
        return RequestInput(
            specialty_ids=specialty_ids,
            city_id=city_id,
            district_id=district_id,
            description=values.description,
            budget_amount=_budget(values.budget_amount),  # type: ignore[arg-type]
            budget_unit=values.budget_unit.strip() or None,
            timing=values.timing,
            # The date input is always submitted; only "date" timing uses it.
            timing_date=(values.timing_date.strip() or None) if values.timing == "date" else None,  # type: ignore[arg-type]
            address=values.address,
        )
    except ValidationError as exc:
        field = str(exc.errors()[0]["loc"][0]) if exc.errors() else ""
        raise RequestError(_FIELD_CODE.get(field, "place")) from exc


def _values_of(view: RequestView) -> FormValues:
    return FormValues(
        specialty=[str(i) for i in view.specialty_ids],
        city=str(view.city_id),
        district=str(view.district_id or ""),
        description=view.description,
        budget_amount=f"{view.budget_amount:f}".rstrip("0").rstrip(".")
        if view.budget_amount is not None
        else "",
        budget_unit=view.budget_unit or "",
        timing=view.timing,
        timing_date=view.timing_date.isoformat() if view.timing_date else "",
        address=view.address or "",
    )


# --- pages ------------------------------------------------------------------------------------


def _form_context(db: Session, request: Request) -> dict[str, object]:
    return {
        "options": request_options.request_form_options(db, lang=current_lang(request)),
        "description_min": DESCRIPTION_RANGE[0],
        "description_max": DESCRIPTION_RANGE[1],
        "address_max": MAX_ADDRESS,
    }


def _new_page(
    request: Request,
    db: Session,
    user: User,
    values: FormValues,
    *,
    status: int = 200,
    error: str | None = None,
) -> HTMLResponse:
    return render(
        request,
        "pages/requests/new.html",
        user=user,
        status=status,
        values=values,
        error=error,
        **_form_context(db, request),
    )


def _detail_page(
    request: Request,
    db: Session,
    user: User,
    request_id: int,
    *,
    status: int = 200,
    error: str | None = None,
    file_errors: list[FileError] | None = None,
    values: FormValues | None = None,
    form_error: str | None = None,
) -> HTMLResponse:
    view = requests.request_for_client(db, user, request_id, lang=current_lang(request))
    done = request.query_params.get("done", "")
    context = _form_context(db, request) if view.editable else {}
    return render(
        request,
        "pages/requests/detail_client.html",
        user=user,
        status=status,
        view=view,
        error=error,
        form_error=form_error,
        file_errors=file_errors or [],
        values=values or _values_of(view),
        done=done if done in DONE else None,
        uploads_enabled=bool(get_settings().uploads_enabled),
        max_photos=MAX_REQUEST_PHOTOS,
        **context,
    )


@router.get(NEW, name="request_new")
def request_new(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    c: Annotated[str | None, Query()] = None,
    s: Annotated[str | None, Query()] = None,
) -> Response:
    values = request_options.prefill_values(db, c, s)
    if user is None:
        # Keep only the slugs that resolved, so the form is prefilled after sign-in.
        kept = {k: v for k, v, ok in (("c", c, values.city), ("s", s, values.specialty)) if ok}
        return _signin(NEW + (f"?{urlencode(kept)}" if kept else ""))
    return _new_page(request, db, user, values)


@router.post(NEW, name="request_create", dependencies=[CSRFProtected])
def request_create(
    request: Request, db: DbSession, user: CurrentUser, values: FormData
) -> Response:
    if user is None:
        return _signin(NEW)
    try:
        created = requests.create_request(db, user, _to_input(values))
    except RequestError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return _not_found(request, user)
        return _new_page(request, db, user, values, status=422, error=_err(exc.code))
    return _redirect(_url(created.id, "?done=created"))


@router.get(MINE, name="requests_mine")
def requests_mine(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin(MINE)
    cards = requests.client_requests(db, user, lang=current_lang(request))
    return render(request, "pages/requests/mine.html", user=user, cards=cards)


@router.get("/requests/{request_id}", name="request_detail")
def request_detail(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId
) -> Response:
    if user is None:
        return _signin(_url(request_id))
    try:
        return _detail_page(request, db, user, request_id)
    except RequestError:
        return _not_found(request, user)


# --- edit -------------------------------------------------------------------------------------


@router.post("/requests/{request_id}/edit", name="request_edit", dependencies=[CSRFProtected])
def request_edit(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId, values: FormData
) -> Response:
    if user is None:
        return _signin(_url(request_id))
    try:
        requests.update_request(db, user, request_id, _to_input(values))
    except RequestError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return _not_found(request, user)
        # A rule broken by the typed values is the form's 422; a state that moved is a 409.
        stale = exc.code in ("locked", "state")
        try:
            return _detail_page(
                request, db, user, request_id,
                status=409 if stale else 422,
                error=_err(exc.code) if stale else None,
                form_error=None if stale else _err(exc.code),
                values=None if stale else values,
            )  # fmt: skip
        except RequestError:
            return _not_found(request, user)
    return _redirect(_url(request_id, "?done=saved"))


# --- photos -----------------------------------------------------------------------------------


class _RecordingStorage:
    """Wraps the storage and remembers the keys put, so a failed commit can remove them."""

    def __init__(self, inner: Storage) -> None:
        self._inner = inner
        self.written: list[str] = []

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._inner.put(key, data, content_type)
        self.written.append(key)

    def delete(self, key: str) -> None:
        self._inner.delete(key)

    def url(self, key: str) -> str:
        return self._inner.url(key)


def _delete_files(storage: Storage, keys: set[str] | list[str]) -> None:
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            log.warning("could not delete a request photo file")


def _read_all(files: list[UploadFile]) -> tuple[list[bytes], list[FileError]]:
    """Each file through `read_limited`; an unreadable one becomes an empty slot plus its error."""
    data: list[bytes] = []
    errors: list[FileError] = []
    for number, upload in enumerate(files, start=1):
        try:
            data.append(read_limited(upload.file))
        except ImageError as exc:
            data.append(b"")
            errors.append((number, f"portfolio.file_err.{exc.code}"))
    return data, errors


def _file_error_key(code: str) -> str:
    return "portfolio.file_err.limit" if code == "photos_limit" else f"portfolio.file_err.{code}"


@router.post("/requests/{request_id}/photos", name="request_photos", dependencies=[CSRFProtected])
def request_photos(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    request_id: RequestId,
    photos: Uploads = None,
) -> Response:
    if not get_settings().uploads_enabled:
        return _not_found(request, user)
    if user is None:
        return _signin(_url(request_id))

    def again(status: int, **kw: object) -> Response:
        try:
            return _detail_page(request, db, user, request_id, status=status, **kw)  # type: ignore[arg-type]
        except RequestError:
            return _not_found(request, user)

    try:
        requests.request_for_client(db, user, request_id)  # ownership first: foreign id is 404
    except RequestError:
        return _not_found(request, user)
    files = [u for u in photos or [] if u.filename]
    if len(files) > MAX_REQUEST_PHOTOS:
        return again(422, error="requests.err.too_many")
    if not files:
        return again(422, error="requests.err.no_files")
    data, errors = _read_all(files)
    unreadable = {number for number, _ in errors}
    storage = _RecordingStorage(get_storage())
    try:
        failed = requests.add_request_photos(db, storage, user, request_id, data)
    except RequestError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return _not_found(request, user)
        return again(409, error=_err(exc.code))
    except (StorageError, OSError, IntegrityError):  # the service already removed its files
        db.rollback()
        log.warning("could not store request photos")
        return again(422, error="requests.err.save")
    except Exception as exc:
        db.rollback()
        _delete_files(storage, storage.written)  # the service cleans up too; this is idempotent
        log.warning("could not store request photos: %s", type(exc).__name__)
        return again(422, error="requests.err.save")
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        _delete_files(storage, storage.written)  # the rows are gone: so must be the files
        log.warning("could not save request photos")
        return again(422, error="requests.err.save")
    errors += [(n, _file_error_key(code)) for n, code in failed if n not in unreadable]
    errors.sort()
    if errors:
        return again(200 if storage.written else 422, file_errors=errors)
    return _redirect(_url(request_id, "?done=photos"))


@router.post(
    "/requests/{request_id}/photos/{photo_id}/delete",
    name="request_photo_delete",
    dependencies=[CSRFProtected],
)
def request_photo_delete(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId, photo_id: PhotoId
) -> Response:
    if user is None:
        return _signin(_url(request_id))
    try:
        keys = requests.delete_request_photo(db, user, request_id, photo_id)
    except RequestError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return _not_found(request, user)
        try:
            return _detail_page(request, db, user, request_id, status=409, error=_err(exc.code))
        except RequestError:
            return _not_found(request, user)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()  # the row stays, so its files stay
        log.warning("could not delete a request photo")
        try:
            return _detail_page(
                request, db, user, request_id, status=422, error="requests.err.save"
            )
        except RequestError:
            return _not_found(request, user)
    _delete_files(get_storage(), keys)  # only now: a failed commit never loses a file
    return _redirect(_url(request_id))


# --- close, cancel, reopen --------------------------------------------------------------------


def _lifecycle(
    request: Request, db: Session, user: User | None, request_id: int, action: str
) -> Response:
    if user is None:
        return _signin(_url(request_id))
    try:
        if action == "reopen":
            requests.reopen_feed(db, user, request_id)
            done = "reopened"
        else:
            reason = "cancelled" if action == "cancel" else "closed"
            requests.close_request(db, user, request_id, reason)
            done = reason
    except RequestError as exc:
        db.rollback()
        if exc.code in ("not_found", "inactive"):
            return _not_found(request, user)
        try:  # a stale double click: say so, change nothing
            return _detail_page(request, db, user, request_id, status=409, error=_err(exc.code))
        except RequestError:
            return _not_found(request, user)
    return _redirect(_url(request_id, f"?done={done}"))


@router.post("/requests/{request_id}/close", name="request_close", dependencies=[CSRFProtected])
def request_close(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId
) -> Response:
    return _lifecycle(request, db, user, request_id, "close")


@router.post("/requests/{request_id}/cancel", name="request_cancel", dependencies=[CSRFProtected])
def request_cancel(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId
) -> Response:
    return _lifecycle(request, db, user, request_id, "cancel")


@router.post("/requests/{request_id}/reopen", name="request_reopen", dependencies=[CSRFProtected])
def request_reopen(
    request: Request, db: DbSession, user: CurrentUser, request_id: RequestId
) -> Response:
    return _lifecycle(request, db, user, request_id, "reopen")
