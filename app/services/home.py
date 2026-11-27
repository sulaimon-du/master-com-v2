"""The home page's masters block (R-028): a few visible masters with quick filters.

Visibility, ordering and cards are the search service's; this module only picks the city chips
and cleans the two parameters (anything unexpected falls back to "all", never an error).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import City
from app.schemas.search import Choice, HomeMasters
from app.services import search

HOME_MASTERS = 8
TOP_CITIES = 4


def top_cities(db: Session, *, lang: str) -> list[Choice]:
    """Active cities with the most visible masters (cached counts); ties keep catalog order."""
    counts = search.cached_counts_by_city(db)
    if not counts:
        return []
    cities = db.scalars(
        select(City)
        .where(City.is_active, City.id.in_(list(counts)))
        .order_by(City.sort_order, City.id)
    ).all()
    ranked = sorted(cities, key=lambda c: -counts[c.id])  # stable sort
    return [
        Choice(slug=c.slug, name=(c.name_tg if lang == "tg" else c.name_ru) or c.name_ru)
        for c in ranked[:TOP_CITIES]
    ]


def home_masters(db: Session, *, lang: str, city: str | None, available: str | None) -> HomeMasters:
    """Up to 8 visible masters, best rated first, in one of the chip cities and/or available.

    A city that is not one of the chips (unknown, inactive, or simply not in the top 4) and any
    `available` other than "1" are ignored.
    """
    cities = top_cities(db, lang=lang)
    chosen = city if city in {c.slug for c in cities} else None
    is_available = available == "1"
    params = {"sort": "rating"}
    if chosen:
        params["c"] = chosen
    if is_available:
        params["available"] = "1"
    query, _ = search.parse_query(params)
    result = search.search(db, query, lang=lang, limit=HOME_MASTERS)
    return HomeMasters(cities=cities, city=chosen, available=is_available, items=result.items)
