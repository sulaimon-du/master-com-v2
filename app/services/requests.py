"""The client's side of a request: post, edit, photos, close, reopen the feed and own views.

Every id from a URL is resolved *through* the client, so a foreign id is "not found".
Lock order: a request row is locked before its photo rows. Callers commit; storage files of a
deleted photo are removed by the caller after the commit (the keys are returned).
"""

import contextlib
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import local_today, utcnow
from app.models import (
    City,
    District,
    JobRequest,
    RequestPhoto,
    RequestSpecialty,
    Specialty,
    User,
)
from app.models.catalog import Category
from app.models.common import PRICE_UNITS
from app.models.request import FEED_PAUSE_STEP, MAX_REQUEST_PHOTOS, TIMINGS
from app.schemas.master import PhotoView
from app.schemas.requests import RequestCard, RequestInput, RequestView
from app.services.auth import is_active
from app.services.images import ImageError, ProcessedImage, process_portfolio
from app.services.storage import Storage, get_storage, new_key

RequestCode = Literal[
    "specialty",
    "place",
    "district_required",
    "description",
    "budget",
    "timing",
    "address",
    "photos_limit",
    "not_found",
    "locked",
    "state",
    "inactive",
]

MAX_SPECIALTIES = 3
DESCRIPTION_RANGE = (20, 2000)
MAX_ADDRESS = 300
MAX_TIMING_DAYS = 180
MAX_BUDGET = Decimal("9999999999.99")  # Numeric(12, 2)


class RequestError(Exception):
    """A rule was broken; `code` says which, so the page can show the matching message."""

    def __init__(self, code: RequestCode) -> None:
        super().__init__(code)
        self.code = code


# --- lookups ----------------------------------------------------------------------------------


def _require_active(user: User) -> None:
    if not is_active(user):
        raise RequestError("inactive")


def _request(db: Session, user: User, request_id: int, *, lock: bool = False) -> JobRequest:
    stmt = select(JobRequest).where(JobRequest.id == request_id, JobRequest.client_id == user.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    request = db.scalar(stmt)
    if request is None:
        raise RequestError("not_found")
    return request


def _require_editable(request: JobRequest) -> None:
    if request.status != "open":
        raise RequestError("state")
    if request.responses_count > 0:
        raise RequestError("locked")


# --- validation -------------------------------------------------------------------------------


def _check_specialties(db: Session, ids: list[int]) -> list[int]:
    ids = list(dict.fromkeys(ids))
    if not 1 <= len(ids) <= MAX_SPECIALTIES:
        raise RequestError("specialty")
    found = set(db.scalars(select(Specialty.id).where(Specialty.id.in_(ids), Specialty.is_active)))
    if found != set(ids):
        raise RequestError("specialty")
    return ids


def _check_place(db: Session, city_id: int, district_id: int | None) -> None:
    city = db.scalar(select(City).where(City.id == city_id, City.is_active))
    if city is None:
        raise RequestError("place")
    has_districts = (
        db.scalar(
            select(District.id).where(District.city_id == city.id, District.is_active).limit(1)
        )
        is not None
    )
    if district_id is None:
        if has_districts:
            raise RequestError("district_required")
        return
    district = db.scalar(
        select(District).where(
            District.id == district_id, District.city_id == city.id, District.is_active
        )
    )
    if district is None:
        raise RequestError("place")


def _check_budget(amount: Decimal | None, unit: str | None) -> None:
    if amount is None and unit is None:
        return
    if amount is None or unit is None or unit not in PRICE_UNITS:
        raise RequestError("budget")
    if not amount.is_finite() or not 0 < amount <= MAX_BUDGET:
        raise RequestError("budget")
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise RequestError("budget")


def _check_timing(data: RequestInput) -> None:
    if data.timing not in TIMINGS:
        raise RequestError("timing")
    if data.timing != "date":
        if data.timing_date is not None:
            raise RequestError("timing")
        return
    today = local_today()  # the client's calendar, not UTC's
    if data.timing_date is None or not (
        today <= data.timing_date <= today + timedelta(days=MAX_TIMING_DAYS)
    ):
        raise RequestError("timing")


def _clean(db: Session, data: RequestInput) -> tuple[list[int], str, str | None]:
    """Validate everything; return the cleaned specialty ids, description and address."""
    specialty_ids = _check_specialties(db, data.specialty_ids)
    _check_place(db, data.city_id, data.district_id)
    description = data.description.strip()
    if not DESCRIPTION_RANGE[0] <= len(description) <= DESCRIPTION_RANGE[1]:
        raise RequestError("description")
    _check_budget(data.budget_amount, data.budget_unit)
    _check_timing(data)
    address = (data.address or "").strip() or None
    if address is not None and len(address) > MAX_ADDRESS:
        raise RequestError("address")
    return specialty_ids, description, address


def _apply(request: JobRequest, data: RequestInput, description: str, address: str | None) -> None:
    request.city_id = data.city_id
    request.district_id = data.district_id
    request.description = description
    request.budget_amount = data.budget_amount
    request.budget_unit = data.budget_unit if data.budget_amount is not None else None
    request.timing = data.timing
    request.timing_date = data.timing_date
    request.address = address


# --- create and edit --------------------------------------------------------------------------


def create_request(db: Session, user: User, data: RequestInput) -> JobRequest:
    _require_active(user)
    specialty_ids, description, address = _clean(db, data)
    request = JobRequest(client_id=user.id)
    _apply(request, data, description, address)
    db.add(request)
    db.flush()
    db.add_all(RequestSpecialty(request_id=request.id, specialty_id=i) for i in specialty_ids)
    db.flush()
    return request


def update_request(db: Session, user: User, request_id: int, data: RequestInput) -> JobRequest:
    """Edit while the request is open and nobody has responded (spec D2)."""
    _require_active(user)
    request = _request(db, user, request_id, lock=True)
    _require_editable(request)
    specialty_ids, description, address = _clean(db, data)
    _apply(request, data, description, address)
    db.execute(delete(RequestSpecialty).where(RequestSpecialty.request_id == request.id))
    db.add_all(RequestSpecialty(request_id=request.id, specialty_id=i) for i in specialty_ids)
    db.flush()
    return request


# --- photos -----------------------------------------------------------------------------------


def add_request_photos(
    db: Session, storage: Storage, user: User, request_id: int, files: list[bytes]
) -> list[tuple[int, str]]:
    """Store up to the free slots of the 4; return (1-based file number, error code) per failure.

    Photos are part of what masters see, so they are locked together with the other fields.
    A storage or database failure removes every file written by this call and propagates.
    """
    _require_active(user)
    _request(db, user, request_id)  # cheap ownership check before heavy work
    errors: list[tuple[int, str]] = []
    processed: list[tuple[int, ProcessedImage, ProcessedImage]] = []
    for number, data in enumerate(files, start=1):
        try:
            full, thumb = process_portfolio(data)
        except ImageError as exc:
            errors.append((number, exc.code))
        else:
            processed.append((number, full, thumb))
    # Two uploads at the limit must not both pass the count: serialise on the request row.
    request = _request(db, user, request_id, lock=True)
    _require_editable(request)
    used = set(
        db.scalars(select(RequestPhoto.position).where(RequestPhoto.request_id == request.id))
    )
    free = [p for p in range(MAX_REQUEST_PHOTOS) if p not in used]
    written: list[str] = []
    try:
        for number, full, thumb in processed:
            if not free:
                errors.append((number, "photos_limit"))
                continue
            key = new_key("r")
            thumb_key = new_key("r", thumb_of=key)
            storage.put(key, full.data, "image/webp")
            written.append(key)
            storage.put(thumb_key, thumb.data, "image/webp")
            written.append(thumb_key)
            with db.begin_nested():
                db.add(
                    RequestPhoto(
                        request_id=request.id,
                        storage_key=key,
                        thumb_key=thumb_key,
                        width=full.width,
                        height=full.height,
                        position=free.pop(0),
                    )
                )
                db.flush()
    except BaseException:
        for written_key in written:
            # Cleanup must not hide the original error.
            with contextlib.suppress(Exception):
                storage.delete(written_key)
        raise
    return sorted(errors)


def delete_request_photo(db: Session, user: User, request_id: int, photo_id: int) -> list[str]:
    """Remove the row (request photos have no undo) and return its storage keys.

    The caller deletes the files after the commit, so a failed commit never loses a file.
    """
    _require_active(user)
    request = _request(db, user, request_id, lock=True)
    _require_editable(request)
    photo = db.scalar(
        select(RequestPhoto).where(
            RequestPhoto.id == photo_id, RequestPhoto.request_id == request.id
        )
    )
    if photo is None:
        raise RequestError("not_found")
    keys = [photo.storage_key, photo.thumb_key]
    db.delete(photo)
    db.flush()
    return keys


# --- lifecycle --------------------------------------------------------------------------------

_CLOSE = {"closed": ("closed", "client_closed"), "cancelled": ("cancelled", "client_cancelled")}


def close_request(
    db: Session, user: User, request_id: int, reason: Literal["closed", "cancelled"]
) -> JobRequest:
    """Close ("no longer needed") or cancel ("posted by mistake") an open or chosen request."""
    _require_active(user)
    request = _request(db, user, request_id, lock=True)
    if reason not in _CLOSE or request.status not in ("open", "master_chosen"):
        raise RequestError("state")
    request.status, request.close_reason = _CLOSE[reason]
    request.closed_at = utcnow()
    db.flush()
    return request


def reopen_feed(db: Session, user: User, request_id: int) -> JobRequest:
    """Put a paused request back in the feed for 15 more responses; restarts the C4 clock."""
    _require_active(user)
    request = _request(db, user, request_id, lock=True)
    if request.status != "open" or request.feed_paused_at is None:
        raise RequestError("state")
    request.feed_paused_at = None
    request.feed_pause_at = request.responses_count + FEED_PAUSE_STEP
    request.opened_at = utcnow()
    request.expiry_reminded_at = None  # the reminder is due again +27 days from now
    db.flush()
    return request


# --- views ------------------------------------------------------------------------------------


def _pick(lang: str, row: City | District | Specialty) -> str:
    return (row.name_tg if lang == "tg" else row.name_ru) or row.name_ru


def _place(db: Session, request: JobRequest, lang: str) -> str:
    city = db.get(City, request.city_id)
    assert city is not None  # noqa: S101  (foreign key)
    if request.district_id is None:
        return _pick(lang, city)
    district = db.get(District, request.district_id)
    assert district is not None  # noqa: S101
    return f"{_pick(lang, city)} — {_pick(lang, district)}"


def _specialties(db: Session, request_ids: list[int]) -> dict[int, list[Specialty]]:
    rows = db.execute(
        select(RequestSpecialty.request_id, Specialty)
        .join(Specialty, Specialty.id == RequestSpecialty.specialty_id)
        .join(Category, Category.id == Specialty.category_id)
        .where(RequestSpecialty.request_id.in_(request_ids))
        .order_by(Category.sort_order, Specialty.sort_order, Specialty.id)
    ).all()
    result: dict[int, list[Specialty]] = {i: [] for i in request_ids}
    for request_id, specialty in rows:
        result[request_id].append(specialty)
    return result


def _photos(db: Session, request_ids: list[int]) -> dict[int, list[RequestPhoto]]:
    result: dict[int, list[RequestPhoto]] = {i: [] for i in request_ids}
    for photo in db.scalars(
        select(RequestPhoto)
        .where(RequestPhoto.request_id.in_(request_ids))
        .order_by(RequestPhoto.position)
    ):
        result[photo.request_id].append(photo)
    return result


def client_requests(db: Session, user: User, *, lang: str = "ru") -> list[RequestCard]:
    """The user's own requests, newest first."""
    requests = db.scalars(
        select(JobRequest)
        .where(JobRequest.client_id == user.id)
        .order_by(JobRequest.created_at.desc(), JobRequest.id.desc())
    ).all()
    ids = [r.id for r in requests]
    specialties, photos, storage = _specialties(db, ids), _photos(db, ids), get_storage()
    return [
        RequestCard(
            id=r.id,
            specialties=[_pick(lang, s) for s in specialties[r.id]],
            place=_place(db, r, lang),
            status=r.status,
            responses_count=r.responses_count,
            created_at=r.created_at,
            thumb_url=storage.url(photos[r.id][0].thumb_key) if photos[r.id] else None,
        )
        for r in requests
    ]


def request_for_client(
    db: Session, user: User, request_id: int, *, lang: str = "ru"
) -> RequestView:
    request = _request(db, user, request_id)
    specialties = _specialties(db, [request.id])[request.id]
    photos = _photos(db, [request.id])[request.id]
    storage = get_storage()
    return RequestView(
        id=request.id,
        specialties=[_pick(lang, s) for s in specialties],
        place=_place(db, request, lang),
        status=request.status,
        responses_count=request.responses_count,
        created_at=request.created_at,
        thumb_url=storage.url(photos[0].thumb_key) if photos else None,
        description=request.description,
        budget_amount=request.budget_amount,
        budget_unit=request.budget_unit,
        timing=request.timing,
        timing_date=request.timing_date,
        address=request.address,
        photos=[
            PhotoView(
                id=p.id,
                url=storage.url(p.storage_key),
                thumb_url=storage.url(p.thumb_key),
                width=p.width,
                height=p.height,
                position=p.position,
                deleted=False,
            )
            for p in photos
        ],
        specialty_ids=[s.id for s in specialties],
        city_id=request.city_id,
        district_id=request.district_id,
        close_reason=request.close_reason,
        feed_paused=request.feed_paused_at is not None,
        editable=request.status == "open" and request.responses_count == 0,
    )
