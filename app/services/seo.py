"""SEO landing pages and page meta (spec D4, D5, D7, D11.1, D11.2, D11.4).

A landing page is a fixed search (specialty + city, or + district) at a clean URL in Russian
(`/<city>/<specialty>`) or Tajik (`/tg/<city>/<specialty>`). The listing is the search
service's; this module adds what makes it a page of its own: the index rule, canonical and
`hreflang` URLs, title, description, Open Graph and links to neighbouring pages.
"""

import re

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.i18n import DEFAULT_LANG, LANGS, translate
from app.models import City, District, Specialty
from app.models.catalog import Category
from app.models.common import REF_SLUG_RE
from app.schemas.search import SearchQuery, SearchResult
from app.services import search

INDEX_MIN = 3  # visible masters a page needs to be indexable (BRIEF 4.14, D5)
NEIGHBOURS = 6  # links per group
DESCRIPTION_MAX = 160
INDEX = "index, follow"
NOINDEX = "noindex, follow"
OG_LOCALES = {"ru": "ru_RU", "tg": "tg_TJ"}
_SLUG = re.compile(REF_SLUG_RE)


class PageMeta(BaseModel):
    """What goes into <head>: every value is plain text, autoescaped by the template."""

    title: str
    description: str
    canonical: str  # absolute
    robots: str
    alternates: dict[str, str] = {}  # hreflang code → absolute URL
    og: dict[str, str] = {}  # Open Graph property ("og:title") → value


class Link(BaseModel):
    label: str
    href: str
    count: int


class LandingView(BaseModel):
    meta: PageMeta
    heading: str
    specialty: str
    city: str
    place: str  # the city, or "district, city"
    result: SearchResult
    lang_links: dict[str, str]  # language → this page in that language (same page number)
    city_link: Link | None  # on a district page: its city page
    districts: list[Link]  # on a city page: its district pages
    other_cities: list[Link]  # the same specialty elsewhere
    other_specialties: list[Link]  # other specialties in this city
    prev_href: str | None
    next_href: str | None


def landing_path(
    lang: str, city: str, specialty: str, district: str | None = None, page: int = 1
) -> str:
    """The page's URL path: `/tg` for Tajik, `?page=N` from page 2."""
    parts = (["tg"] if lang == "tg" else []) + [city, *([district] if district else []), specialty]
    path = "/" + "/".join(parts)
    return f"{path}?page={page}" if page > 1 else path


def absolute(path: str) -> str:
    return get_settings().site_url.rstrip("/") + path


def clip(text: str, limit: int = DESCRIPTION_MAX) -> str:
    """At most `limit` characters, cut at a word boundary with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:—-") + "…"


def _name(lang: str, row: City | District | Specialty) -> str:
    return (row.name_tg if lang == "tg" else row.name_ru) or row.name_ru


def _is_slug(value: str | None) -> bool:
    return value is None or (len(value) <= search.SLUG_MAX and bool(_SLUG.fullmatch(value)))


def _resolve(
    db: Session, city: str, district: str | None, specialty: str
) -> tuple[City, District | None, Specialty] | None:
    """Active rows for the slugs; a district must belong to the city."""
    if not (_is_slug(city) and _is_slug(district) and _is_slug(specialty)):
        return None
    found_city = db.scalar(select(City).where(City.slug == city, City.is_active))
    found_specialty = db.scalar(
        select(Specialty)
        .join(Category, Category.id == Specialty.category_id)
        .where(Specialty.slug == specialty, Specialty.is_active, Category.is_active)
    )
    if found_city is None or found_specialty is None:
        return None
    found_district = None
    if district is not None:
        found_district = db.scalar(
            select(District).where(
                District.city_id == found_city.id, District.slug == district, District.is_active
            )
        )
        if found_district is None:
            return None
    return found_city, found_district, found_specialty


def _top(items: list[tuple[int, int, Link]]) -> list[Link]:
    """By count, then the catalog's order; at most NEIGHBOURS."""
    return [link for _, _, link in sorted(items, key=lambda i: (-i[2].count, i[0], i[1]))][
        :NEIGHBOURS
    ]


def _neighbours(
    db: Session, lang: str, city: City, district: District | None, specialty: Specialty
) -> tuple[Link | None, list[Link], list[Link], list[Link]]:
    """Links to pages that list at least one visible master; inactive rows are never linked
    (the grouped counts do not look at `is_active`, so it is checked here)."""
    by_city = search.counts_by_city_specialty(db)
    cities = {c.id: c for c in db.scalars(select(City).where(City.is_active))}
    specialties = {
        s.id: (category_order, s)
        for s, category_order in db.execute(
            select(Specialty, Category.sort_order)
            .join(Category, Category.id == Specialty.category_id)
            .where(Specialty.is_active, Category.is_active)
        )
    }

    other_cities = []
    other_specialties = []
    for (city_id, specialty_id), n in by_city.items():
        if n < 1:
            continue
        if specialty_id == specialty.id and city_id != city.id and city_id in cities:
            other = cities[city_id]
            link = Link(
                label=_name(lang, other),
                href=landing_path(lang, other.slug, specialty.slug),
                count=n,
            )
            other_cities.append((other.sort_order, other.id, link))
        elif city_id == city.id and specialty_id != specialty.id and specialty_id in specialties:
            category_order, other_specialty = specialties[specialty_id]
            link = Link(
                label=_name(lang, other_specialty),
                href=landing_path(lang, city.slug, other_specialty.slug),
                count=n,
            )
            other_specialties.append((category_order, other_specialty.sort_order, link))

    city_link = None
    districts: list[Link] = []
    if district is not None:
        n = by_city.get((city.id, specialty.id), 0)
        if n >= 1:
            city_link = Link(
                label=_name(lang, city), href=landing_path(lang, city.slug, specialty.slug), count=n
            )
    else:
        # Only districts some master named: whole-city masters alone would make every district
        # page a near-duplicate of this one. The pill shows what the district page lists
        # (named + whole-city, D11.2).
        named = search.counts_by_district_specialty(db, named_only=True)
        listed = search.counts_by_district_specialty(db, named_only=False)
        for row in db.scalars(
            select(District)
            .where(District.city_id == city.id, District.is_active)
            .order_by(District.sort_order, District.id)
        ):
            if named.get((row.id, specialty.id), 0) >= 1 and len(districts) < NEIGHBOURS:
                districts.append(
                    Link(
                        label=_name(lang, row),
                        href=landing_path(lang, city.slug, specialty.slug, row.slug),
                        count=listed.get((row.id, specialty.id), 0),
                    )
                )
    return city_link, districts, _top(other_cities), _top(other_specialties)


def landing(
    db: Session, *, lang: str, city: str, district: str | None, specialty: str, page: int
) -> LandingView | None:
    """The landing page for these slugs, or None when any of them is unknown or inactive."""
    found = _resolve(db, city, district, specialty)
    if found is None:
        return None
    city_row, district_row, specialty_row = found
    result = search.search(
        db,
        SearchQuery(
            q=None,
            specialty=specialty_row.slug,
            category=None,
            city=city_row.slug,
            district=district_row.slug if district_row else None,
            available=False,
            sort=None,
            page=page,
        ),
        lang=lang,
    )
    result.notices = []  # a page past the end shows the last one; no notice on these pages
    if district_row is None:
        named = result.total  # the city page counts what it lists (D11.2)
    else:
        named = search.count_visible(
            db,
            specialty_id=specialty_row.id,
            city_id=city_row.id,
            district_id=district_row.id,
            named_district_only=True,
        )

    def path(code: str, number: int) -> str:
        return landing_path(
            code,
            city_row.slug,
            specialty_row.slug,
            district_row.slug if district_row else None,
            number,
        )

    specialty_name = _name(lang, specialty_row)
    place = _name(lang, city_row)
    if district_row is not None:
        place = translate(
            lang, "seo.place_district", district=_name(lang, district_row), city=place
        )
    params = {"specialty": specialty_name, "place": place, "n": str(result.total)}
    title = translate(lang, "seo.title", app=get_settings().app_name, **params)
    description = clip(
        translate(lang, "seo.description" if result.total else "seo.description_empty", **params)
    )
    canonical = absolute(path(lang, result.page))
    alternates = {code: absolute(path(code, result.page)) for code in LANGS}
    alternates["x-default"] = alternates[DEFAULT_LANG]
    meta = PageMeta(
        title=title,
        description=description,
        canonical=canonical,
        robots=INDEX if named >= INDEX_MIN and result.page == 1 else NOINDEX,
        alternates=alternates,
        og={
            "og:title": title,
            "og:description": description,
            "og:url": canonical,
            "og:type": "website",
            "og:locale": OG_LOCALES[lang],
        },
    )
    city_link, districts, other_cities, other_specialties = _neighbours(
        db, lang, city_row, district_row, specialty_row
    )
    return LandingView(
        meta=meta,
        heading=translate(lang, "seo.heading", **params),
        specialty=specialty_name,
        city=_name(lang, city_row),
        place=place,
        result=result,
        lang_links={code: path(code, result.page) for code in LANGS},
        city_link=city_link,
        districts=districts,
        other_cities=other_cities,
        other_specialties=other_specialties,
        prev_href=path(lang, result.page - 1) if result.page > 1 else None,
        next_href=path(lang, result.page + 1) if result.page < result.pages else None,
    )
