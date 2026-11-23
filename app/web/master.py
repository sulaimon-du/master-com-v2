"""Master onboarding wizard (steps 1-4), profile editing and the avatar upload.

Step 5 (the first collection) lives in app/web/portfolio.py.

Every route acts on the signed-in user's own profile: there is no master ID in any URL or form,
so one user cannot reach another's profile. Rules live in app/services/masters.py.
"""

import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import DbSession
from app.models import MasterProfile
from app.models.user import User
from app.schemas.master import BasicsForm, BasicsValues, ContactsValues
from app.services import auth, master_forms, masters
from app.services.images import ImageError, process_avatar, read_limited
from app.services.masters import MasterError
from app.services.storage import StorageError, get_storage, new_key
from app.web.auth import _attach_session, notice_page
from app.web.cookies import SESSION, cookie_name
from app.web.csrf import CSRFProtected
from app.web.deps import current_lang, current_user
from app.web.render import render

log = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]

STEP_TEMPLATES = {
    "basics": "pages/master/onboarding_basics.html",
    "specialties": "pages/master/onboarding_specialties.html",
    "area": "pages/master/onboarding_area.html",
    "phone": "pages/master/onboarding_phone.html",
}
BASICS_FIELDS = ("display_name", "slug", "headline", "experience_years", "bio")


def _signin(path: str) -> RedirectResponse:
    return RedirectResponse(f"/signin?next={path}", status_code=303)


def _to_basics() -> RedirectResponse:
    return RedirectResponse("/master/onboarding/basics", status_code=303)


def _not_found(request: Request, user: User | None) -> HTMLResponse:
    return notice_page(request, user, status=404, title="error.not_found", text="error.not_found")


def _after(next_: str, step: str) -> RedirectResponse:
    """Where a saved step goes: back to the edit page, or on to the next wizard step."""
    target = "/master/edit?saved=1" if next_ == "edit" else f"/master/onboarding/{step}"
    return RedirectResponse(target, status_code=303)


def _error_key(code: str) -> str:
    return f"master.err.{code}"


def _ids(values: list[str]) -> list[int] | None:
    """Form ids as integers; None when any value is not a plain number."""
    if not all(v.isascii() and v.isdigit() and len(v) < 10 for v in values):
        return None
    return [int(v) for v in values]


def _page(
    request: Request,
    db: Session,
    user: User,
    master: MasterProfile | None,
    template: str,
    *,
    status: int = 200,
    next_: str = "",
    section: str = "",
    error: str | None = None,
    errors: dict[str, str] | None = None,
    basics: BasicsValues | None = None,
    contacts: ContactsValues | None = None,
    avatar_error: str | None = None,
    **extra: object,
) -> HTMLResponse:
    """One context for every wizard step and the edit page, so a form renders the same anywhere."""
    lang = current_lang(request)
    settings = get_settings()
    values = basics
    if values is None:
        if master is not None:
            values = master_forms.basics_values(master)
        else:
            name = (user.name or "").strip()[:80]
            values = BasicsValues(
                display_name=name, slug=masters.suggest_slug(db, name) if name else ""
            )
    return render(
        request,
        template,
        user=user,
        status=status,
        has_profile=master is not None,
        state=master_forms.profile_state(db, master) if master is not None else None,
        basics=values,
        contacts=contacts
        or (master_forms.contacts_values(master) if master is not None else ContactsValues()),
        categories=master_forms.specialty_options(db, master, lang),
        cities=master_forms.area_options(db, master, lang),
        uploads_enabled=bool(settings.uploads_enabled),
        next_=next_,
        section=section,
        error=error,
        errors=errors or {},
        avatar_error=avatar_error,
        saved=request.query_params.get("saved") == "1",
        **extra,
    )


def _step_page(
    request: Request,
    db: Session,
    user: User,
    master: MasterProfile | None,
    step: str,
    *,
    next_: str = "",
    **kwargs: object,
) -> HTMLResponse:
    template = "pages/master/edit.html" if next_ == "edit" else STEP_TEMPLATES[step]
    return _page(request, db, user, master, template, next_=next_, section=step, **kwargs)  # type: ignore[arg-type]


# --- step 1: basics ---------------------------------------------------------------------------


@router.get("/master/onboarding/basics", name="master_basics")
def master_basics(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin("/master/onboarding/basics")
    master = masters.master_for_user(db, user)
    return _step_page(request, db, user, master, "basics")


@router.post("/master/onboarding/basics", name="master_basics_submit", dependencies=[CSRFProtected])
def master_basics_submit(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    display_name: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    headline: Annotated[str, Form()] = "",
    experience_years: Annotated[str, Form()] = "",
    is_available: Annotated[str | None, Form()] = None,
    bio: Annotated[str | None, Form()] = None,
    next: str = "",
) -> Response:
    if user is None:
        return _signin("/master/onboarding/basics")
    master = masters.master_for_user(db, user)
    if next == "edit" and master is None:
        return _to_basics()
    submitted = BasicsValues(
        display_name=display_name,
        slug=slug,
        headline=headline,
        experience_years=experience_years,
        is_available=is_available is not None,
        bio=bio if bio is not None else (master.bio or "" if master else ""),
    )

    def again(status: int, errors: dict[str, str]) -> Response:
        return _step_page(
            request, db, user, master, "basics", next_=next, status=status, basics=submitted,
            errors=errors,
        )  # fmt: skip

    try:
        form = BasicsForm(
            display_name=display_name,
            slug=slug.strip().lower(),
            headline=headline,
            experience_years=experience_years.strip() or None,
            is_available=is_available is not None,
            bio=submitted.bio,
        )
    except ValidationError as exc:
        fields = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
        return again(422, {f: f"validation.{f}" for f in fields if f in BASICS_FIELDS})
    try:
        if master is None:
            return _create(request, db, user, form)
        masters.update_basics(db, master, form.to_input(), bio=form.bio)
    except MasterError as exc:
        field = "slug" if exc.code in ("slug_taken", "slug_invalid") else "form"
        status = 409 if exc.code == "slug_taken" else 422
        return again(status, {field: _error_key(exc.code)})
    return _after(next, "specialties")


def _create(request: Request, db: Session, user: User, form: BasicsForm) -> Response:
    try:
        masters.create_profile(db, user, form.to_input())
    except MasterError as exc:
        if exc.code == "exists":  # a double submit: the profile is there, carry on
            return _after("", "specialties")
        raise
    # Creating the profile is a privilege change: new session ID and CSRF token, once (R-011).
    settings = get_settings()
    raw = auth.open_session(db, user, replace=request.cookies.get(cookie_name(SESSION, settings)))
    response = _after("", "specialties")
    _attach_session(response, raw, settings)
    return response


@router.get("/master/slug-check", name="master_slug_check")
def master_slug_check(
    request: Request, db: DbSession, user: CurrentUser, slug: str = ""
) -> Response:
    if user is None:
        return _signin("/master/onboarding/basics")
    master = masters.master_for_user(db, user)
    slug = slug.strip().lower()[:80]
    state = masters.check_slug(db, slug, own_id=master.id if master else None)
    suggestion = masters.suggest_slug(db, slug) if state == "taken" else None
    return render(
        request, "partials/slug_check.html", user=user, state=state, suggestion=suggestion
    )


# --- step 2: specialties ----------------------------------------------------------------------


@router.get("/master/onboarding/specialties", name="master_specialties")
def master_specialties(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin("/master/onboarding/specialties")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    return _step_page(request, db, user, master, "specialties")


@router.post(
    "/master/onboarding/specialties", name="master_specialties_submit", dependencies=[CSRFProtected]
)
def master_specialties_submit(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    specialty: Annotated[list[str] | None, Form()] = None,
    primary: Annotated[list[str] | None, Form()] = None,
    next: str = "",
) -> Response:
    if user is None:
        return _signin("/master/onboarding/specialties")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    chosen, firsts = _ids(specialty or []), _ids(primary or [])
    try:
        if chosen is None or firsts is None:
            raise MasterError("unknown")
        masters.set_specialties(db, master, chosen, firsts)
    except MasterError as exc:
        db.rollback()
        return _step_page(
            request, db, user, master, "specialties", next_=next, status=422,
            error=_error_key(exc.code),
        )  # fmt: skip
    return _after(next, "area")


# --- step 3: work area ------------------------------------------------------------------------


@router.get("/master/onboarding/area", name="master_area")
def master_area(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin("/master/onboarding/area")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    return _step_page(request, db, user, master, "area")


@router.post("/master/onboarding/area", name="master_area_submit", dependencies=[CSRFProtected])
def master_area_submit(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    city: Annotated[list[str] | None, Form()] = None,
    district: Annotated[list[str] | None, Form()] = None,
    next: str = "",
) -> Response:
    if user is None:
        return _signin("/master/onboarding/area")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    cities, districts = _ids(city or []), _ids(district or [])
    try:
        if cities is None or districts is None:
            raise MasterError("area")
        if not cities and not districts:
            raise MasterError("area")
        masters.set_areas(db, master, cities, districts)
    except MasterError as exc:
        db.rollback()
        return _step_page(
            request, db, user, master, "area", next_=next, status=422, error=_error_key(exc.code)
        )
    return _after(next, "phone")


# --- step 4: phone and contacts ---------------------------------------------------------------


@router.get("/master/onboarding/phone", name="master_phone")
def master_phone(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin("/master/onboarding/phone")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    return _step_page(request, db, user, master, "phone")


@router.post(
    "/master/onboarding/phone/check", name="master_phone_check", dependencies=[CSRFProtected]
)
def master_phone_check(db: DbSession, user: CurrentUser, next: str = "") -> Response:
    """ "Check again": a phone verified in Telegram meanwhile may now publish the profile."""
    if user is None:
        return _signin("/master/onboarding/phone")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    masters.refresh_publication(db, master)
    if next == "edit":
        return RedirectResponse("/master/edit?saved=1", status_code=303)
    return RedirectResponse("/master/onboarding/phone", status_code=303)


@router.post("/master/onboarding/phone", name="master_phone_submit", dependencies=[CSRFProtected])
def master_phone_submit(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    telegram: Annotated[str, Form()] = "",
    whatsapp: Annotated[str, Form()] = "",
    instagram: Annotated[str, Form()] = "",
    next: str = "",
) -> Response:
    if user is None:
        return _signin("/master/onboarding/phone")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    try:
        masters.set_contacts(db, master, telegram=telegram, whatsapp=whatsapp, instagram=instagram)
    except MasterError as exc:
        db.rollback()
        return _step_page(
            request, db, user, master, "phone", next_=next, status=422,
            error=_error_key(exc.code), errors={exc.code: _error_key(exc.code)},
            contacts=ContactsValues(telegram=telegram, whatsapp=whatsapp, instagram=instagram),
        )  # fmt: skip
    masters.refresh_publication(db, master)
    if next == "edit":
        return RedirectResponse("/master/edit?saved=1", status_code=303)
    return RedirectResponse("/master/onboarding/collection", status_code=303)


def finish_onboarding(db: Session, master: MasterProfile) -> RedirectResponse:
    """The end of the wizard (step 5 or "Skip"): publish when ready, then show the profile."""
    published = masters.refresh_publication(db, master)
    notice = "published" if published else "incomplete"
    return RedirectResponse(f"/m/{master.slug}?notice={notice}", status_code=303)


# --- edit page and avatar ---------------------------------------------------------------------


@router.get("/master/edit", name="master_edit")
def master_edit(request: Request, db: DbSession, user: CurrentUser) -> Response:
    if user is None:
        return _signin("/master/edit")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    return _page(request, db, user, master, "pages/master/edit.html", next_="edit")


@router.post("/master/avatar", name="master_avatar", dependencies=[CSRFProtected])
def master_avatar(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    upload: Annotated[UploadFile | None, File()] = None,
    next: str = "",
) -> Response:
    if not get_settings().uploads_enabled:
        return _not_found(request, user)
    if user is None:
        return _signin("/master/edit")
    master = masters.master_for_user(db, user)
    if master is None:
        return _to_basics()
    try:
        if upload is None:
            raise ImageError("type")
        # The only read of the upload: capped, and the bytes live only in this function.
        processed = process_avatar(read_limited(upload.file))
    except ImageError as exc:
        return _step_page(
            request, db, user, master, "basics", next_=next, status=422,
            avatar_error=f"master.avatar_err.{exc.code}",
        )  # fmt: skip
    storage = get_storage()
    key = new_key("a")
    try:
        storage.put(key, processed.data, "image/webp")
    except (StorageError, OSError):
        # Backend detail stays out of the page and the log; the user may simply retry.
        log.warning("could not store an avatar")
        return _step_page(
            request, db, user, master, "basics", next_=next, status=503,
            avatar_error="master.avatar_err.save",
        )  # fmt: skip
    try:
        old = masters.set_avatar(db, master, key)
        db.commit()
    except BaseException:
        db.rollback()
        with contextlib.suppress(Exception):  # cleanup must not hide the original error
            storage.delete(key)
        raise
    if old is not None:
        try:
            storage.delete(old)
        except (StorageError, OSError, ValueError):
            log.warning("could not delete the replaced avatar")
    target = "/master/edit?saved=1" if next == "edit" else "/master/onboarding/basics"
    return RedirectResponse(target, status_code=303)
