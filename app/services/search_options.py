"""Catalog and places for the search form and the home page (active rows only, no writes)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import City, District, Specialty
from app.models.catalog import Category
from app.schemas.search import CategoryChoice, Choice, SearchOptions


def _pick(lang: str, name_ru: str, name_tg: str) -> str:
    return (name_tg if lang == "tg" else name_ru) or name_ru


def search_options(db: Session, *, lang: str, city: str | None = None) -> SearchOptions:
    """Directions with their specialties, cities, and the districts of `city` (a slug)."""
    groups: dict[int, CategoryChoice] = {}
    for category, specialty in db.execute(
        select(Category, Specialty)
        .outerjoin(Specialty, (Specialty.category_id == Category.id) & Specialty.is_active)
        .where(Category.is_active)
        .order_by(Category.sort_order, Category.id, Specialty.sort_order, Specialty.id)
    ):
        group = groups.setdefault(
            category.id,
            CategoryChoice(
                slug=category.slug,
                name=_pick(lang, category.name_ru, category.name_tg),
                specialties=[],
            ),
        )
        if specialty is not None:
            group.specialties.append(
                Choice(slug=specialty.slug, name=_pick(lang, specialty.name_ru, specialty.name_tg))
            )
    cities = list(db.scalars(select(City).where(City.is_active).order_by(City.sort_order, City.id)))
    districts: list[Choice] = []
    selected = next((c for c in cities if c.slug == city), None)
    if selected is not None:
        districts = [
            Choice(slug=d.slug, name=_pick(lang, d.name_ru, d.name_tg))
            for d in db.scalars(
                select(District)
                .where(District.city_id == selected.id, District.is_active)
                .order_by(District.sort_order, District.id)
            )
        ]
    return SearchOptions(
        categories=list(groups.values()),
        cities=[Choice(slug=c.slug, name=_pick(lang, c.name_ru, c.name_tg)) for c in cities],
        districts=districts,
    )
