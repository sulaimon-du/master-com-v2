"""Choices for the request form and the slug prefill (active rows only, no writes)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import City, District, Specialty
from app.models.catalog import Category
from app.schemas.requests import (
    DistrictGroup,
    FormValues,
    IdChoice,
    RequestFormOptions,
    SpecialtyGroup,
)
from app.services import search


def _pick(lang: str, row: City | District | Specialty | Category) -> str:
    return (row.name_tg if lang == "tg" else row.name_ru) or row.name_ru


def request_form_options(db: Session, *, lang: str) -> RequestFormOptions:
    groups: dict[int, SpecialtyGroup] = {}
    for category, specialty in db.execute(
        select(Category, Specialty)
        .join(Specialty, (Specialty.category_id == Category.id) & Specialty.is_active)
        .where(Category.is_active)
        .order_by(Category.sort_order, Category.id, Specialty.sort_order, Specialty.id)
    ):
        group = groups.setdefault(
            category.id, SpecialtyGroup(name=_pick(lang, category), specialties=[])
        )
        group.specialties.append(IdChoice(id=specialty.id, name=_pick(lang, specialty)))
    cities = list(db.scalars(select(City).where(City.is_active).order_by(City.sort_order, City.id)))
    by_city: dict[int, DistrictGroup] = {}
    for city, district in db.execute(
        select(City, District)
        .join(District, (District.city_id == City.id) & District.is_active)
        .where(City.is_active)
        .order_by(City.sort_order, City.id, District.sort_order, District.id)
    ):
        group = by_city.setdefault(city.id, DistrictGroup(city=_pick(lang, city), districts=[]))
        group.districts.append(IdChoice(id=district.id, name=_pick(lang, district)))
    return RequestFormOptions(
        categories=list(groups.values()),
        cities=[IdChoice(id=c.id, name=_pick(lang, c)) for c in cities],
        district_groups=list(by_city.values()),
    )


def prefill_values(db: Session, city_slug: str | None, specialty_slug: str | None) -> FormValues:
    """A new form with the city and specialty the slugs name; invalid slugs are ignored."""
    values = FormValues()
    if city_slug and len(city_slug) <= search.SLUG_MAX:
        city_id = db.scalar(select(City.id).where(City.slug == city_slug, City.is_active))
        if city_id is not None:
            values.city = str(city_id)
    if specialty_slug and len(specialty_slug) <= search.SLUG_MAX:
        specialty_id = db.scalar(
            select(Specialty.id)
            .join(Category, Category.id == Specialty.category_id)
            .where(Specialty.slug == specialty_slug, Specialty.is_active, Category.is_active)
        )
        if specialty_id is not None:
            values.specialty = [str(specialty_id)]
    return values
