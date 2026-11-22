"""Portfolio management pages and onboarding step 5.

Every route acts on the signed-in user's own master profile; collection and photo ids from the URL
are only ever resolved through app/services/portfolio.py, so a foreign id is "not found" (404).
"""

import logging
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.body_limit import MAX_FILES_PER_UPLOAD
from app.core.config import get_settings
from app.core.db import DbSession
from app.models import MasterProfile
from app.models.user import User
from app.schemas.master import CollectionCard, PhotoView
from app.services import masters, portfolio
from app.services.images import ImageError, read_limited
from app.services.portfolio import PortfolioCode, PortfolioError
from app.services.storage import get_storage
from app.web.csrf import CSRFProtected
from app.web.deps import current_user
from app.web.master import _not_found, _signin, _to_basics, finish_onboarding
from app.web.render import render

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]
Uploads = Annotated[list[UploadFile] | None, File()]

PORTFOLIO = "/master/portfolio"
STEP_PATH = "/master/onboarding/collection"
ERROR_CODES = set(get_args(PortfolioCode))

# An error shown next to one uploaded file: (position of the file in the form, translation key).
FileError = tuple[int, str]


def _collection_url(collection_id: int, query: str = "") -> str:
    return f"{PORTFOLIO}/collections/{collection_id}{query}"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _gate(db: Session, user: User | None, path: str) -> tuple[User, MasterProfile] | Response:
    """The signed-in user and their own profile, or the redirect that sends them where to go."""
    if user is None:
        return _signin(path)
    master = masters.master_for_user(db, user)
    return (user, master) if master is not None else _to_basics()


def _card(db: Session, master: MasterProfile, collection_id: int) -> CollectionCard:
    for card in portfolio.collections_for_owner(db, master):
        if card.id == collection_id:
            return card
    raise PortfolioError("not_found")


def _err_key(code: str) -> str:
    return f"portfolio.err.{code}"


# --- pages ------------------------------------------------------------------------------------


def _list_page(
    request: Request,
    db: Session,
    user: User,
    master: MasterProfile,
    *,
    status: int = 200,
    error: str | None = None,
    title: str = "",
    description: str = "",
) -> HTMLResponse:
    return render(
        request,
        "pages/master/portfolio.html",
        user=user,
        status=status,
        collections=portfolio.collections_for_owner(db, master),
        error=error,
        form_title=title,
        form_description=description,
    )


def _collection_page(
    request: Request,
    db: Session,
    user: User,
    master: MasterProfile,
    collection_id: int,
    *,
    status: int = 200,
    error: str | None = None,
    file_errors: list[FileError] | None = None,
    title: str | None = None,
    description: str | None = None,
    finishing: bool = False,
) -> HTMLResponse:
    card = _card(db, master, collection_id)
    params = request.query_params
    undo = params.get("undo", "")
    query_error = params.get("error", "")
    if error is None and query_error in ERROR_CODES:
        error = _err_key(query_error)
    return render(
        request,
        "pages/master/collection_edit.html",
        user=user,
        status=status,
        collection=card,
        photos=portfolio.collection_photos(db, master, collection_id, owner=True),
        uploads_enabled=bool(get_settings().uploads_enabled),
        max_files=MAX_FILES_PER_UPLOAD,
        error=error,
        file_errors=file_errors or [],
        form_title=card.title if title is None else title,
        form_description=(card.description or "") if description is None else description,
        undo_id=int(undo) if undo.isascii() and undo.isdigit() and len(undo) < 10 else None,
        saved=params.get("saved") == "1",
        finishing=finishing,
    )


def _step_page(
    request: Request,
    user: User,
    *,
    status: int = 200,
    error: str | None = None,
    file_errors: list[FileError] | None = None,
    title: str = "",
    description: str = "",
) -> HTMLResponse:
    return render(
        request,
        "pages/master/onboarding_collection.html",
        user=user,
        status=status,
        uploads_enabled=bool(get_settings().uploads_enabled),
        max_files=MAX_FILES_PER_UPLOAD,
        error=error,
        file_errors=file_errors or [],
        form_title=title,
        form_description=description,
    )


def _tile(request: Request, photo: PhotoView, collection_id: int, **extra: object) -> HTMLResponse:
    return render(
        request, "partials/photo_tile.html", photo=photo, collection_id=collection_id, **extra
    )


def _undo_fragment(request: Request, photo_id: int, error: str | None = None) -> HTMLResponse:
    return render(request, "partials/photo_undo.html", photo_id=photo_id, error=error)


# --- uploads ----------------------------------------------------------------------------------


def _too_many(uploads: list[UploadFile] | None) -> bool:
    return len([u for u in uploads or [] if u.filename]) > MAX_FILES_PER_UPLOAD


def _save_files(
    db: Session, master: MasterProfile, collection_id: int, uploads: list[UploadFile] | None
) -> tuple[int, list[FileError]]:
    """Process each file on its own and commit it: bad files are reported, good ones are kept.

    Each file is read only through `read_limited`; the bytes live inside this loop and nothing
    the client sent (name, content type) is stored or shown. Errors are listed by position.
    """
    storage = get_storage()
    saved = 0
    errors: list[FileError] = []
    for position, upload in enumerate(uploads or [], start=1):
        if not upload.filename:  # an empty file input is submitted as one nameless part
            continue
        try:
            portfolio.add_photo(db, storage, master, collection_id, read_limited(upload.file))
            db.commit()
            saved += 1
        except ImageError as exc:
            db.rollback()
            errors.append((position, f"portfolio.file_err.{exc.code}"))
        except PortfolioError as exc:
            db.rollback()
            if exc.code == "not_found":
                raise
            errors.append((position, "portfolio.file_err.limit"))
        except IntegrityError:  # a storage-key collision: practically impossible, never a 500
            db.rollback()
            log.warning("portfolio photo insert hit an integrity error")
            errors.append((position, "portfolio.file_err.save"))
    return saved, errors


# --- portfolio list and collections -----------------------------------------------------------


@router.get(PORTFOLIO, name="portfolio_page")
def portfolio_page(request: Request, db: DbSession, user: CurrentUser) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    return _list_page(request, db, user, master)


@router.post(f"{PORTFOLIO}/collections", name="portfolio_create", dependencies=[CSRFProtected])
def portfolio_create(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        collection = portfolio.create_collection(db, master, title, description)
    except PortfolioError as exc:
        db.rollback()
        return _list_page(
            request, db, user, master, status=422, error=_err_key(exc.code),
            title=title, description=description,
        )  # fmt: skip
    return _redirect(_collection_url(collection.id))


@router.get(f"{PORTFOLIO}/collections/{{collection_id}}", name="portfolio_collection")
def portfolio_collection(
    request: Request, db: DbSession, user: CurrentUser, collection_id: int
) -> Response:
    gate = _gate(db, user, _collection_url(collection_id))
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        return _collection_page(request, db, user, master, collection_id)
    except PortfolioError:
        return _not_found(request, user)


@router.post(
    f"{PORTFOLIO}/collections/{{collection_id}}",
    name="portfolio_update",
    dependencies=[CSRFProtected],
)
def portfolio_update(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    collection_id: int,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
) -> Response:
    gate = _gate(db, user, _collection_url(collection_id))
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        portfolio.update_collection(db, master, collection_id, title=title, description=description)
    except PortfolioError as exc:
        db.rollback()
        if exc.code == "not_found":
            return _not_found(request, user)
        return _collection_page(
            request, db, user, master, collection_id, status=422, error=_err_key(exc.code),
            title=title, description=description,
        )  # fmt: skip
    return _redirect(_collection_url(collection_id, "?saved=1"))


@router.post(
    f"{PORTFOLIO}/collections/{{collection_id}}/hide",
    name="portfolio_hide",
    dependencies=[CSRFProtected],
)
def portfolio_hide(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    collection_id: int,
    hidden: Annotated[str, Form()] = "1",
) -> Response:
    gate = _gate(db, user, _collection_url(collection_id))
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        portfolio.set_collection_hidden(db, master, collection_id, hidden != "0")
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    return _redirect(_collection_url(collection_id, "?saved=1"))


@router.post(
    f"{PORTFOLIO}/collections/{{collection_id}}/move",
    name="portfolio_move",
    dependencies=[CSRFProtected],
)
def portfolio_move(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    collection_id: int,
    direction: Annotated[Literal["up", "down"], Query(alias="dir")],
) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        portfolio.move_collection(db, master, collection_id, direction)
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    return _redirect(PORTFOLIO)


@router.post(
    f"{PORTFOLIO}/collections/{{collection_id}}/photos",
    name="portfolio_upload",
    dependencies=[CSRFProtected],
)
def portfolio_upload(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    collection_id: int,
    photos: Uploads = None,
) -> Response:
    if not get_settings().uploads_enabled:
        return _not_found(request, user)
    gate = _gate(db, user, _collection_url(collection_id))
    if isinstance(gate, Response):
        return gate
    user, master = gate

    def again(status: int, **kw: object) -> Response:
        return _collection_page(request, db, user, master, collection_id, status=status, **kw)  # type: ignore[arg-type]

    try:
        _card(db, master, collection_id)  # ownership first: a foreign id is 404 whatever is sent
        if _too_many(photos):
            return again(422, error=_err_key("too_many"))
        if not any(u.filename for u in photos or []):
            return again(422, error=_err_key("no_files"))
        saved, errors = _save_files(db, master, collection_id, photos)
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    if errors:
        return again(200 if saved else 422, file_errors=errors)
    return _redirect(_collection_url(collection_id))


# --- photos -----------------------------------------------------------------------------------


@router.post(
    f"{PORTFOLIO}/photos/{{photo_id}}/move",
    name="portfolio_photo_move",
    dependencies=[CSRFProtected],
)
def portfolio_photo_move(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    photo_id: int,
    direction: Annotated[Literal["left", "right"], Query(alias="dir")],
) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        collection_id = portfolio.photo_collection_id(db, master, photo_id)
        portfolio.move_photo(db, master, photo_id, direction)
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    return _redirect(_collection_url(collection_id))


@router.post(
    f"{PORTFOLIO}/photos/{{photo_id}}/delete",
    name="portfolio_photo_delete",
    dependencies=[CSRFProtected],
)
def portfolio_photo_delete(
    request: Request, db: DbSession, user: CurrentUser, photo_id: int
) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        collection_id = portfolio.photo_collection_id(db, master, photo_id)
        portfolio.delete_photo(db, master, photo_id)
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    if _is_htmx(request):
        return _undo_fragment(request, photo_id)
    return _redirect(_collection_url(collection_id, f"?undo={photo_id}"))


@router.post(
    f"{PORTFOLIO}/photos/{{photo_id}}/undo",
    name="portfolio_photo_undo",
    dependencies=[CSRFProtected],
)
def portfolio_photo_undo(
    request: Request, db: DbSession, user: CurrentUser, photo_id: int
) -> Response:
    gate = _gate(db, user, PORTFOLIO)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    try:
        collection_id = portfolio.photo_collection_id(db, master, photo_id)
        try:
            portfolio.undo_delete_photo(db, master, photo_id)
        except PortfolioError as exc:
            db.rollback()
            if exc.code == "not_found":
                raise
            # htmx swaps only 2xx answers, so the message travels in a 200 fragment.
            if _is_htmx(request):
                return _undo_fragment(request, photo_id, error=_err_key(exc.code))
            return _redirect(_collection_url(collection_id, f"?error={exc.code}"))
        tile = next(
            (
                p
                for p in portfolio.collection_photos(db, master, collection_id, owner=True)
                if p.id == photo_id
            ),
            None,
        )
    except PortfolioError:
        db.rollback()
        return _not_found(request, user)
    if _is_htmx(request) and tile is not None:
        return _tile(request, tile, collection_id)
    return _redirect(_collection_url(collection_id))


# --- onboarding step 5: first collection ------------------------------------------------------


@router.get(STEP_PATH, name="master_collection")
def master_collection(request: Request, db: DbSession, user: CurrentUser) -> Response:
    gate = _gate(db, user, STEP_PATH)
    if isinstance(gate, Response):
        return gate
    user, _master = gate
    return _step_page(request, user)


@router.post(STEP_PATH, name="master_collection_submit", dependencies=[CSRFProtected])
def master_collection_submit(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    skip: Annotated[str | None, Form()] = None,
    photos: Uploads = None,
) -> Response:
    """Create the first collection (with photos), or skip it; either way the wizard finishes."""
    gate = _gate(db, user, STEP_PATH)
    if isinstance(gate, Response):
        return gate
    user, master = gate
    if skip is not None:
        return finish_onboarding(db, master)
    uploads_enabled = bool(get_settings().uploads_enabled)
    if uploads_enabled and _too_many(photos):
        return _step_page(
            request, user, status=422, error=_err_key("too_many"), title=title,
            description=description,
        )  # fmt: skip
    try:
        collection = portfolio.create_collection(db, master, title, description)
        db.commit()
    except PortfolioError as exc:
        db.rollback()
        return _step_page(
            request, user, status=422, error=_err_key(exc.code), title=title,
            description=description,
        )  # fmt: skip
    if uploads_enabled:
        _saved, errors = _save_files(db, master, collection.id, photos)
        if errors:
            # The collection exists now: show what failed on its own page, where the master can
            # retry the files, and let them finish the wizard from there.
            return _collection_page(
                request, db, user, master, collection.id, file_errors=errors, finishing=True
            )
    return finish_onboarding(db, master)
