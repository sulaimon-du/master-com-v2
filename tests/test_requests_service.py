import io
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import local_today, utcnow
from app.models import (
    City,
    District,
    JobRequest,
    JobResponse,
    RequestPhoto,
    RequestSpecialty,
    Specialty,
    User,
)
from app.schemas.requests import RequestInput
from app.services import requests as svc
from app.services.masters import BasicsInput, create_profile
from app.services.requests import RequestError
from app.services.storage import KEY_RE, LocalDiskStorage, new_key


def _user(db: Session, *, blocked: bool = False) -> User:
    n = uuid.uuid4().hex[:10]
    user = User(email=f"{n}@example.com", name="Клиент", blocked_at=utcnow() if blocked else None)
    db.add(user)
    db.flush()
    return user


def _specs(db: Session, n: int = 1) -> list[int]:
    return list(
        db.scalars(select(Specialty.id).where(Specialty.is_active).order_by(Specialty.id).limit(n))
    )


def _city_with_districts(db: Session) -> City:
    return db.scalars(select(City).where(City.slug == "dushanbe")).one()


def _city_without_districts(db: Session) -> City:
    has = select(District.city_id).where(District.is_active)
    return db.scalars(
        select(City).where(City.is_active, City.id.not_in(has)).order_by(City.id).limit(1)
    ).one()


def _district(db: Session, city: City) -> District:
    return db.scalars(
        select(District).where(District.city_id == city.id, District.is_active).limit(1)
    ).one()


def _input(db: Session, **kw) -> RequestInput:
    city = kw.pop("city", None) or _city_without_districts(db)
    base = {
        "specialty_ids": _specs(db),
        "city_id": city.id,
        "district_id": None,
        "description": "Нужно переложить плитку в ванной комнате",
        "timing": "flexible",
    }
    return RequestInput(**{**base, **kw})


def _make(db: Session, user: User | None = None, **kw) -> tuple[User, JobRequest]:
    user = user or _user(db)
    return user, svc.create_request(db, user, _input(db, **kw))


def _master(db: Session):
    n = uuid.uuid4().hex[:10]
    owner = User(email=f"{n}@example.com", name="Мастер")
    db.add(owner)
    db.flush()
    return create_profile(
        db,
        owner,
        BasicsInput(
            display_name="Мастер",
            slug=f"m-{n}",
            headline=None,
            experience_years=1,
            is_available=True,
        ),
    )


def _respond(db: Session, request: JobRequest) -> JobResponse:
    response = JobResponse(request_id=request.id, master_id=_master(db).id)
    db.add(response)
    request.responses_count += 1
    db.flush()
    return response


def _png(size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def _code(db: Session, fn, *args, **kw) -> str:
    with pytest.raises(RequestError) as exc:
        fn(*args, **kw)
    return exc.value.code


# --- storage keys -----------------------------------------------------------------------------


def test_request_keys_match_regex() -> None:
    key = new_key("r")
    assert KEY_RE.fullmatch(key)
    assert KEY_RE.fullmatch(new_key("r", thumb_of=key))
    import re

    assert re.fullmatch(r"r/[0-9]{4}/[0-9]{2}/[0-9a-f]{32}\.webp", key)
    assert not KEY_RE.fullmatch("r/٢٠٢٦/١١/" + "a" * 32 + ".webp")


# --- create and validation --------------------------------------------------------------------


def test_create_request(db: Session) -> None:
    user, request = _make(
        db,
        specialty_ids=_specs(db, 2),
        description="  Нужно переложить плитку в ванной комнате  ",
        budget_amount=Decimal("1500"),
        budget_unit="fixed",
        address="  ул. Рудаки 1  ",
    )
    assert request.client_id == user.id
    assert request.status == "open"
    assert request.description == "Нужно переложить плитку в ванной комнате"
    assert request.address == "ул. Рудаки 1"
    assert request.feed_pause_at == 15 and request.opened_at is not None
    assert set(
        db.scalars(
            select(RequestSpecialty.specialty_id).where(RequestSpecialty.request_id == request.id)
        )
    ) == set(_specs(db, 2))


def test_specialty_codes(db: Session) -> None:
    user = _user(db)
    for ids in ([], _specs(db, 4), [10**9]):
        assert _code(db, svc.create_request, db, user, _input(db, specialty_ids=ids)) == "specialty"
    # duplicates collapse
    one = _specs(db)[0]
    request = svc.create_request(db, user, _input(db, specialty_ids=[one, one]))
    assert db.scalar(select(func.count()).where(RequestSpecialty.request_id == request.id)) == 1


def test_inactive_specialty_refused(db: Session) -> None:
    spec = db.get(Specialty, _specs(db)[0])
    spec.is_active = False
    db.flush()
    assert (
        _code(db, svc.create_request, db, _user(db), _input(db, specialty_ids=[spec.id]))
        == "specialty"
    )


def test_specialty_under_an_inactive_category_refused(db: Session) -> None:
    spec = db.get(Specialty, _specs(db)[0])
    spec.category.is_active = False
    db.flush()
    assert (
        _code(db, svc.create_request, db, _user(db), _input(db, specialty_ids=[spec.id]))
        == "specialty"
    )


def test_place_rules(db: Session) -> None:
    user = _user(db)
    with_d, without_d = _city_with_districts(db), _city_without_districts(db)
    d = _district(db, with_d)
    # district required iff the city has districts
    assert _code(db, svc.create_request, db, user, _input(db, city=with_d)) == "district_required"
    assert (
        svc.create_request(db, user, _input(db, city=with_d, district_id=d.id)).district_id == d.id
    )
    # a district of another city, and a district for a city that has none
    assert (
        _code(db, svc.create_request, db, user, _input(db, city=without_d, district_id=d.id))
        == "place"
    )
    other = db.scalars(
        select(City).where(City.id != with_d.id, City.id.in_(select(District.city_id))).limit(1)
    ).first()
    if other is not None:
        assert (
            _code(db, svc.create_request, db, user, _input(db, city=other, district_id=d.id))
            == "place"
        )
    # unknown city
    assert (
        _code(db, svc.create_request, db, user, _input(db, city=without_d, city_id=10**9))
        == "place"
    )


def test_inactive_places_refused(db: Session) -> None:
    user = _user(db)
    city = _city_without_districts(db)
    city.is_active = False
    db.flush()
    assert _code(db, svc.create_request, db, user, _input(db, city=city)) == "place"
    with_d = _city_with_districts(db)
    d = _district(db, with_d)
    d.is_active = False
    db.flush()
    assert (
        _code(db, svc.create_request, db, user, _input(db, city=with_d, district_id=d.id))
        == "place"
    )


def test_description_bounds(db: Session) -> None:
    user = _user(db)
    for text in ("", "коротко", " " * 30, "x" * 2001):
        assert (
            _code(db, svc.create_request, db, user, _input(db, description=text)) == "description"
        )
    assert svc.create_request(db, user, _input(db, description="x" * 20))
    assert svc.create_request(db, user, _input(db, description="x" * 2000))


def test_budget_rules(db: Session) -> None:
    user = _user(db)
    bad = [
        {"budget_amount": Decimal("100")},  # no unit
        {"budget_unit": "fixed"},  # no amount
        {"budget_amount": Decimal("0"), "budget_unit": "fixed"},
        {"budget_amount": Decimal("-5"), "budget_unit": "fixed"},
        {"budget_amount": Decimal("100"), "budget_unit": "yard"},
        {"budget_amount": Decimal("1e12"), "budget_unit": "fixed"},
        {"budget_amount": Decimal("1.234"), "budget_unit": "fixed"},
    ]
    for kw in bad:
        assert _code(db, svc.create_request, db, user, _input(db, **kw)) == "budget", kw
    ok = svc.create_request(db, user, _input(db, budget_amount=Decimal("50.5"), budget_unit="m2"))
    assert ok.budget_amount == Decimal("50.50") and ok.budget_unit == "m2"


def test_timing_rules(db: Session) -> None:
    user = _user(db)
    today = local_today()
    bad = [
        {"timing": "date"},
        {"timing": "date", "timing_date": today - timedelta(days=1)},
        {"timing": "date", "timing_date": today + timedelta(days=181)},
        {"timing": "urgent", "timing_date": today},
        {"timing": "later"},
    ]
    for kw in bad:
        assert _code(db, svc.create_request, db, user, _input(db, **kw)) == "timing", kw
    for day in (today, today + timedelta(days=180)):
        assert (
            svc.create_request(db, user, _input(db, timing="date", timing_date=day)).timing_date
            == day
        )
    assert svc.create_request(db, user, _input(db, timing="urgent")).timing == "urgent"


def test_address_rules(db: Session) -> None:
    user = _user(db)
    assert _code(db, svc.create_request, db, user, _input(db, address="x" * 301)) == "address"
    assert svc.create_request(db, user, _input(db, address="x" * 300))
    assert svc.create_request(db, user, _input(db, address="   ")).address is None


def test_blocked_user_cannot_create(db: Session) -> None:
    blocked = _user(db, blocked=True)
    assert _code(db, svc.create_request, db, blocked, _input(db)) == "inactive"
    assert db.scalar(select(func.count()).where(JobRequest.client_id == blocked.id)) == 0


def test_blocked_user_cannot_write(db: Session) -> None:
    user, request = _make(db)
    user.blocked_at = utcnow()
    db.flush()
    assert _code(db, svc.update_request, db, user, request.id, _input(db)) == "inactive"
    assert _code(db, svc.close_request, db, user, request.id, "closed") == "inactive"
    assert (
        _code(
            db,
            svc.add_request_photos,
            db,
            LocalDiskStorage("/nonexistent"),
            user,
            request.id,
            [b"x"],
        )
        == "inactive"
    )


# --- update -----------------------------------------------------------------------------------


def test_update_replaces_fields_and_specialties(db: Session) -> None:
    user, request = _make(db)
    ids = _specs(db, 2)
    updated = svc.update_request(
        db,
        user,
        request.id,
        _input(
            db, specialty_ids=ids[1:], description="Совсем другое описание работы", timing="urgent"
        ),
    )
    assert updated.description == "Совсем другое описание работы" and updated.timing == "urgent"
    assert set(
        db.scalars(
            select(RequestSpecialty.specialty_id).where(RequestSpecialty.request_id == request.id)
        )
    ) == set(ids[1:])


def test_update_validates(db: Session) -> None:
    user, request = _make(db)
    assert (
        _code(db, svc.update_request, db, user, request.id, _input(db, description="коротко"))
        == "description"
    )


def test_update_locked_after_a_response(db: Session) -> None:
    user, request = _make(db)
    _respond(db, request)
    assert _code(db, svc.update_request, db, user, request.id, _input(db)) == "locked"


def test_update_not_open_is_state(db: Session) -> None:
    user, request = _make(db)
    svc.close_request(db, user, request.id, "closed")
    assert _code(db, svc.update_request, db, user, request.id, _input(db)) == "state"


# --- close / cancel ---------------------------------------------------------------------------


def _in_state(db: Session, status: str) -> tuple[User, JobRequest]:
    user, request = _make(db)
    if status in ("master_chosen", "completed"):
        request.chosen_master_id = _master(db).id
        request.master_chosen_at = utcnow()
        request.client_phone = "+992901234567"
    if status == "completed":
        request.completed_at = utcnow()
    if status in ("closed", "cancelled"):
        request.closed_at = utcnow()
        request.close_reason = "client_closed" if status == "closed" else "client_cancelled"
    request.status = status
    db.flush()
    return user, request


@pytest.mark.parametrize("start", ["open", "master_chosen"])
@pytest.mark.parametrize(
    ("reason", "status", "why"),
    [("closed", "closed", "client_closed"), ("cancelled", "cancelled", "client_cancelled")],
)
def test_close_and_cancel_allowed(
    db: Session, start: str, reason: str, status: str, why: str
) -> None:
    user, request = _in_state(db, start)
    result = svc.close_request(db, user, request.id, reason)
    assert (result.status, result.close_reason) == (status, why)
    assert result.closed_at is not None


@pytest.mark.parametrize("start", ["completed", "closed", "cancelled"])
@pytest.mark.parametrize("reason", ["closed", "cancelled"])
def test_close_refused_from_final_states(db: Session, start: str, reason: str) -> None:
    user, request = _in_state(db, start)
    assert _code(db, svc.close_request, db, user, request.id, reason) == "state"
    db.refresh(request)
    assert request.status == start


def test_close_unknown_reason(db: Session) -> None:
    user, request = _make(db)
    assert _code(db, svc.close_request, db, user, request.id, "bogus") == "state"


# --- reopen -----------------------------------------------------------------------------------


def test_reopen_feed_math_and_clock(db: Session) -> None:
    user, request = _make(db)
    for _ in range(15):
        _respond(db, request)
    request.feed_paused_at = utcnow()
    request.opened_at = utcnow() - timedelta(days=20)
    request.expiry_reminded_at = utcnow() - timedelta(days=1)
    old_opened = request.opened_at
    db.flush()
    result = svc.reopen_feed(db, user, request.id)
    assert result.feed_paused_at is None
    assert result.feed_pause_at == 30
    assert result.opened_at > old_opened + timedelta(days=19)
    assert result.expiry_reminded_at is None
    assert result.responses_count == 15
    # a second click: nothing to reopen any more
    assert _code(db, svc.reopen_feed, db, user, request.id) == "state"


def test_reopen_needs_open_and_paused(db: Session) -> None:
    user, request = _make(db)
    assert _code(db, svc.reopen_feed, db, user, request.id) == "state"  # not paused
    request.feed_paused_at = utcnow()
    db.flush()
    svc.close_request(db, user, request.id, "closed")
    assert _code(db, svc.reopen_feed, db, user, request.id) == "state"  # not open


# --- photos -----------------------------------------------------------------------------------


def _photos(db: Session, request: JobRequest) -> list[RequestPhoto]:
    db.expire_all()
    return list(
        db.scalars(
            select(RequestPhoto)
            .where(RequestPhoto.request_id == request.id)
            .order_by(RequestPhoto.position)
        )
    )


def _disk(tmp_path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "store")


def _files(root) -> list[str]:
    return sorted(p.name for p in root.rglob("*.webp"))


def test_add_photos_and_limit(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    errors = svc.add_request_photos(db, storage, user, request.id, [_png()] * 3)
    assert errors == []
    photos = _photos(db, request)
    assert [p.position for p in photos] == [0, 1, 2]
    assert all(KEY_RE.fullmatch(p.storage_key) and p.storage_key.startswith("r/") for p in photos)
    assert all(p.thumb_key == p.storage_key.removesuffix(".webp") + "-t.webp" for p in photos)
    assert len(_files(tmp_path / "store")) == 6
    # two more: first fits, second is over the cap (1-based position within this call)
    errors = svc.add_request_photos(db, storage, user, request.id, [_png(), _png()])
    assert errors == [(2, "photos_limit")]
    assert len(_photos(db, request)) == 4
    assert len(_files(tmp_path / "store")) == 8


def test_bad_files_reported_by_position(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    errors = svc.add_request_photos(
        db, storage, user, request.id, [_png(), b"not an image", _png()]
    )
    assert errors == [(2, "type")]
    assert [p.position for p in _photos(db, request)] == [0, 1]


def test_delete_frees_lowest_slot(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    svc.add_request_photos(db, storage, user, request.id, [_png()] * 4)
    second = _photos(db, request)[1]
    keys = svc.delete_request_photo(db, user, request.id, second.id)
    assert sorted(keys) == sorted([second.storage_key, second.thumb_key])
    assert db.get(RequestPhoto, second.id) is None
    assert svc.add_request_photos(db, storage, user, request.id, [_png()]) == []
    assert [p.position for p in _photos(db, request)] == [0, 1, 2, 3]
    assert _photos(db, request)[1].id != second.id


def test_photos_locked_after_a_response(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    svc.add_request_photos(db, storage, user, request.id, [_png()])
    _respond(db, request)
    assert _code(db, svc.add_request_photos, db, storage, user, request.id, [_png()]) == "locked"
    photo = _photos(db, request)[0]
    assert _code(db, svc.delete_request_photo, db, user, request.id, photo.id) == "locked"


@pytest.mark.parametrize("closed", [False, True])
def test_locked_or_closed_request_skips_image_processing(
    db: Session, tmp_path, monkeypatch, closed: bool
) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    if closed:
        svc.close_request(db, user, request.id, "closed")
    else:
        _respond(db, request)

    def boom(data):  # type: ignore[no-untyped-def]
        raise AssertionError("process_portfolio must not run")

    monkeypatch.setattr(svc, "process_portfolio", boom)
    code = _code(db, svc.add_request_photos, db, storage, user, request.id, [_png()])
    assert code == ("state" if closed else "locked")


def test_photo_db_failure_cleans_storage(db: Session, tmp_path, monkeypatch) -> None:
    storage = _disk(tmp_path)
    user, request = _make(db)
    real_flush = db.flush
    seen = {"photos": 0}

    def flaky(*a, **kw):
        if any(isinstance(o, RequestPhoto) for o in db.new):
            seen["photos"] += 1
            if seen["photos"] == 2:  # the second photo's insert fails
                raise RuntimeError("db down")
        return real_flush(*a, **kw)

    monkeypatch.setattr(db, "flush", flaky)
    with pytest.raises(RuntimeError):
        svc.add_request_photos(db, storage, user, request.id, [_png(), _png()])
    monkeypatch.undo()
    assert _files(tmp_path / "store") == []  # files of both uploads are gone


def test_photo_storage_failure_cleans_storage(db: Session, tmp_path) -> None:
    class Flaky(LocalDiskStorage):
        def __init__(self, root) -> None:
            super().__init__(root)
            self.puts = 0

        def put(self, key, data, content_type):
            self.puts += 1
            if self.puts == 4:  # second photo's thumbnail
                raise OSError("disk full")
            super().put(key, data, content_type)

    storage = Flaky(tmp_path / "store")
    user, request = _make(db)
    with pytest.raises(OSError):
        svc.add_request_photos(db, storage, user, request.id, [_png(), _png()])
    assert _files(tmp_path / "store") == []


# --- foreign ids ------------------------------------------------------------------------------


def test_foreign_ids_are_not_found(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    owner, request = _make(db)
    svc.add_request_photos(db, storage, owner, request.id, [_png()])
    photo = _photos(db, request)[0]
    other = _user(db)
    request.feed_paused_at = utcnow()
    db.flush()
    calls = [
        (svc.update_request, (db, other, request.id, _input(db))),
        (svc.add_request_photos, (db, storage, other, request.id, [_png()])),
        (svc.delete_request_photo, (db, other, request.id, photo.id)),
        (svc.close_request, (db, other, request.id, "closed")),
        (svc.close_request, (db, other, request.id, "cancelled")),
        (svc.reopen_feed, (db, other, request.id)),
        (svc.request_for_client, (db, other, request.id)),
        (svc.update_request, (db, owner, 10**9, _input(db))),
        (svc.delete_request_photo, (db, owner, request.id, 10**9)),
    ]
    for fn, args in calls:
        assert _code(db, fn, *args) == "not_found", fn.__name__
    # the photo of another request of the same owner is not reachable through this request
    _, mine = _make(db, owner)
    assert _code(db, svc.delete_request_photo, db, owner, mine.id, photo.id) == "not_found"
    db.refresh(request)
    assert request.status == "open" and request.feed_paused_at is not None
    assert len(_photos(db, request)) == 1


# --- views ------------------------------------------------------------------------------------


def test_client_requests_newest_first_only_own(db: Session, tmp_path) -> None:
    storage = _disk(tmp_path)
    user, first = _make(db)
    _, second = _make(db, user)
    _make(db)  # someone else's
    svc.add_request_photos(db, storage, user, second.id, [_png()])
    cards = svc.client_requests(db, user, lang="ru")
    assert [c.id for c in cards] == [second.id, first.id]
    top = cards[0]
    assert top.status == "open" and top.responses_count == 0
    assert top.thumb_url is not None and top.thumb_url.startswith("/media/r/")
    assert cards[1].thumb_url is None
    assert top.specialties and top.place


def test_card_and_view_languages_and_fields(db: Session) -> None:
    user, request = _make(
        db,
        city=_city_with_districts(db),
        district_id=_district(db, _city_with_districts(db)).id,
        budget_amount=Decimal("700"),
        budget_unit="hour",
        address="ул. Рудаки 1",
        timing="date",
        timing_date=local_today() + timedelta(days=3),
    )
    view = svc.request_for_client(db, user, request.id, lang="tg")
    assert view.address == "ул. Рудаки 1"
    assert view.description.startswith("Нужно")
    assert view.budget_amount == Decimal("700.00") and view.budget_unit == "hour"
    assert view.timing == "date" and view.timing_date == request.timing_date
    assert view.editable is True and view.feed_paused is False
    city = db.get(City, request.city_id)
    assert (city.name_tg or city.name_ru) in view.place
    ru = svc.request_for_client(db, user, request.id, lang="ru")
    assert city.name_ru in ru.place
    _respond(db, request)
    assert svc.request_for_client(db, user, request.id).editable is False


def test_timing_window_uses_the_local_date_not_utc(db: Session, monkeypatch) -> None:
    """01:00 on 2 Dec in Dushanbe (UTC+5) is still 1 Dec in UTC: 1 Dec is yesterday there."""
    from datetime import UTC, date, datetime

    monkeypatch.setattr("app.core.time.utcnow", lambda: datetime(2026, 12, 1, 20, 0, tzinfo=UTC))
    assert local_today() == date(2026, 12, 2)
    user = _user(db)
    yesterday = date(2026, 12, 1)  # still "today" in UTC
    assert (
        _code(db, svc.create_request, db, user, _input(db, timing="date", timing_date=yesterday))
        == "timing"
    )
    last = date(2026, 12, 2) + timedelta(days=180)
    ok = svc.create_request(db, user, _input(db, timing="date", timing_date=last))
    assert ok.timing_date == last
    too_far = last + timedelta(days=1)
    assert (
        _code(db, svc.create_request, db, user, _input(db, timing="date", timing_date=too_far))
        == "timing"
    )
