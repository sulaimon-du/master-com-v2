"""The sitemap (spec D6, D11.5): indexable SEO pages in ru and tg, visible profiles and public
collections, as absolute URLs. Visibility is the search service's own (`search._visible`, which
re-checks the owner), so a master blocked straight in SQL drops out when the cache expires.

`lastmod`: profiles use `updated_at`, collections the newest visible photo. SEO pages carry none:
the newest `updated_at` among their masters would need one more grouped query per page set.
"""

import logging
from collections.abc import Iterator
from datetime import datetime
from itertools import chain
from xml.sax.saxutils import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.i18n import DEFAULT_LANG, LANGS
from app.models import (
    City,
    District,
    MasterProfile,
    MasterSearch,
    PortfolioCollection,
    PortfolioPhoto,
    Specialty,
)
from app.models.catalog import Category
from app.services import cache, search
from app.services.seo import INDEX_MIN, absolute, landing_path

log = logging.getLogger(__name__)

MAX_URLS = 50_000
TTL = 600  # seconds
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
XHTML_NS = "http://www.w3.org/1999/xhtml"
QUOTE = {'"': "&quot;"}

# (loc, lastmod, alternates: hreflang → absolute URL)
Entry = tuple[str, datetime | None, dict[str, str]]


def _seo_entries(db: Session) -> Iterator[Entry]:
    cities = {c.id: c for c in db.scalars(select(City).where(City.is_active).order_by(City.id))}
    specialties = {
        s.id: s
        for s in db.scalars(
            select(Specialty)
            .join(Category, Category.id == Specialty.category_id)
            .where(Specialty.is_active, Category.is_active)
            .order_by(Specialty.id)
        )
    }
    pages: list[tuple[str, str, str | None]] = []  # (city, specialty, district)
    for (city_id, specialty_id), n in sorted(search.cached_counts_by_city_specialty(db).items()):
        if n >= INDEX_MIN and city_id in cities and specialty_id in specialties:
            pages.append((cities[city_id].slug, specialties[specialty_id].slug, None))
    districts = {
        d.id: (d, cities[d.city_id])
        for d in db.scalars(select(District).where(District.is_active).order_by(District.id))
        if d.city_id in cities
    }
    named = search.cached_counts_by_district_specialty(db, named_only=True)
    for (district_id, specialty_id), n in sorted(named.items()):
        if n >= INDEX_MIN and district_id in districts and specialty_id in specialties:
            district, city = districts[district_id]
            pages.append((city.slug, specialties[specialty_id].slug, district.slug))
    for city, specialty, district in pages:
        urls = {lang: absolute(landing_path(lang, city, specialty, district)) for lang in LANGS}
        alternates = {**urls, "x-default": urls[DEFAULT_LANG]}
        for lang in LANGS:
            yield urls[lang], None, alternates


def _profile_entries(db: Session) -> Iterator[Entry]:
    stmt = search._visible(
        select(MasterProfile.slug, MasterProfile.updated_at).select_from(MasterSearch)
    ).order_by(MasterProfile.id)
    for slug, updated_at in db.execute(stmt):
        yield absolute(f"/m/{slug}"), updated_at, {}


def _collection_entries(db: Session) -> Iterator[Entry]:
    newest = func.max(PortfolioPhoto.created_at)
    stmt = (
        search._visible(
            select(MasterProfile.slug, PortfolioCollection.id, newest).select_from(MasterSearch)
        )
        .join(PortfolioCollection, PortfolioCollection.master_id == MasterProfile.id)
        .join(PortfolioPhoto, PortfolioPhoto.collection_id == PortfolioCollection.id)
        .where(
            PortfolioCollection.hidden_at.is_(None),
            PortfolioPhoto.deleted_at.is_(None),
            PortfolioPhoto.hidden_at.is_(None),
        )
        .group_by(MasterProfile.id, MasterProfile.slug, PortfolioCollection.id)
        .order_by(MasterProfile.id, PortfolioCollection.id)
    )
    for slug, collection_id, photo_at in db.execute(stmt):
        yield absolute(f"/m/{slug}/c/{collection_id}"), photo_at, {}


def _lastmod(value: datetime) -> str:
    return value.date().isoformat()


def _entry_xml(loc: str, lastmod: datetime | None, alternates: dict[str, str]) -> str:
    parts = [f"<loc>{escape(loc)}</loc>"]
    if lastmod is not None:
        parts.append(f"<lastmod>{_lastmod(lastmod)}</lastmod>")
    for hreflang, href in alternates.items():
        parts.append(
            f'<xhtml:link rel="alternate" hreflang="{escape(hreflang, QUOTE)}" '
            f'href="{escape(href, QUOTE)}"/>'
        )
    return f"<url>{''.join(parts)}</url>"


def build(db: Session) -> bytes:
    """The sitemap XML, at most MAX_URLS entries (SEO pages first, then profiles, collections).

    Written by hand with escaping: the root must always declare `xmlns:xhtml`, which an
    ElementTree serializer drops when no alternate is present."""
    rows: list[str] = []
    for loc, lastmod, alternates in chain(
        _seo_entries(db), _profile_entries(db), _collection_entries(db)
    ):
        if len(rows) >= MAX_URLS:
            log.warning("sitemap truncated at the limit of %d URLs", MAX_URLS)
            break
        rows.append(_entry_xml(loc, lastmod, alternates))
    body = "".join(rows)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{SITEMAP_NS}" xmlns:xhtml="{XHTML_NS}">{body}</urlset>\n'
    )
    return xml.encode("utf-8")


def cached(db: Session) -> bytes:
    """The sitemap, rebuilt at most every ten minutes."""
    return cache.get_or_compute(("sitemap",), TTL, lambda: build(db))
