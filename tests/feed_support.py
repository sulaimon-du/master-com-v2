"""Helpers shared by the feed and responses tests: visible masters and requests to match."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import City, District, JobRequest, MasterProfile, Specialty, User
from app.schemas.requests import RequestInput
from app.services import masters
from app.services import requests as request_svc
from app.services.masters import BasicsInput


def hide_other_requests(db: Session) -> None:
    """Only the requests a test creates are open (rolled back with the test)."""
    db.execute(text("UPDATE requests SET hidden_at = now() WHERE hidden_at IS NULL"))


def spec(db: Session, slug: str) -> Specialty:
    return db.scalars(select(Specialty).where(Specialty.slug == slug)).one()


def city(db: Session, slug: str) -> City:
    return db.scalars(select(City).where(City.slug == slug)).one()


def district(db: Session, city_slug: str, slug: str) -> District:
    return db.scalars(
        select(District).join(City).where(City.slug == city_slug, District.slug == slug)
    ).one()


def city_without_districts(db: Session) -> City:
    has = select(District.city_id).where(District.is_active)
    return db.scalars(
        select(City).where(City.is_active, City.id.not_in(has)).order_by(City.id).limit(1)
    ).one()


def user(db: Session, *, name: str = "Клиент") -> User:
    n = uuid.uuid4().hex[:10]
    row = User(email=f"{n}@example.com", name=name)
    db.add(row)
    db.flush()
    return row


def master(
    db: Session,
    *,
    specs: tuple[str, ...] = ("santekhnik",),
    cities: tuple[str, ...] = (),
    districts: tuple[tuple[str, str], ...] = (),
    city_ids: tuple[int, ...] = (),
    ready: bool = True,
) -> tuple[User, MasterProfile]:
    """A master made through the masters service; published (visible) when `ready`."""
    n = uuid.uuid4().hex[:10]
    owner = User(email=f"{n}@example.com", name="Мастер")
    owner.phone = "+99290" + str(int(n, 16))[:7].ljust(7, "0")
    owner.phone_verified_at = utcnow()
    db.add(owner)
    db.flush()
    profile = masters.create_profile(
        db,
        owner,
        BasicsInput(
            display_name="Мастер",
            slug=f"m-{n}",
            headline=None,
            experience_years=3,
            is_available=True,
        ),
    )
    if ready:
        masters.set_specialties(
            db, profile, [spec(db, s).id for s in specs], [spec(db, specs[0]).id]
        )
        masters.set_areas(
            db,
            profile,
            [city(db, c).id for c in cities] + list(city_ids),
            [district(db, c, d).id for c, d in districts],
        )
    assert (profile.published_at is not None) == ready
    return owner, profile


def post(
    db: Session,
    client: User | None = None,
    *,
    specs: tuple[str, ...] = ("santekhnik",),
    city_id: int,
    district_id: int | None = None,
    **kw,
) -> JobRequest:
    client = client or user(db)
    data = RequestInput(
        specialty_ids=[spec(db, s).id for s in specs],
        city_id=city_id,
        district_id=district_id,
        description=kw.pop("description", "Нужно заменить смеситель на кухне, срочно"),
        timing=kw.pop("timing", "flexible"),
        **kw,
    )
    return request_svc.create_request(db, client, data)
