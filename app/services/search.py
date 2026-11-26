"""Finding masters (spec D2, D3, D9, D11): filters, typo-tolerant free text, hints, counts.

Every query reads `master_search` for visible rows and also joins the owner to re-check that the
account is active, so a block written straight to the database hides the master at once.
Free text is transliterated to Latin words that are bound as parameters and matched with pg_trgm
(`word <% document`); user input never becomes a LIKE pattern or a regular expression.
"""

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg.errors import QueryCanceled
from sqlalchemy import ColumnElement, Select, any_, bindparam, case, distinct, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from app.models import (
    City,
    District,
    MasterArea,
    MasterProfile,
    MasterSearch,
    MasterSpecialty,
    PortfolioCollection,
    PortfolioPhoto,
    Specialty,
    User,
)
from app.models.catalog import Category, SpecialtySynonym
from app.models.common import REF_SLUG_RE
from app.models.search import PlaceSynonym
from app.schemas.search import (
    MasterCard,
    SearchQuery,
    SearchResult,
    Segment,
    Sort,
    SpecialtyHint,
    Suggestion,
)
from app.services import cache
from app.services.storage import get_storage
from app.services.translit import translit

PAGE_SIZE = 20
MAX_PAGE = 50
MAX_QUERY = 100
MAX_WORDS = 6
MIN_WORD = 2
CARD_SPECIALTIES = 3
CARD_PHOTOS = 4
# Thresholds (spec "left to the builder"; final values recorded in R-025).
WORD_THRESHOLD = 0.4  # pg_trgm word_similarity of each query word against the document
HINT_THRESHOLD = 0.6  # trigram similarity of the query to a specialty name or synonym
SUGGEST_THRESHOLD = 0.3  # "did you mean" against specialty and place names
STATEMENT_TIMEOUT = "2s"
SORTS: tuple[Sort, ...] = ("relevance", "rating", "newest")
# Every code `parse_query` and `search` can put in `notices`; the page has a text for each.
NOTICE_CODES = (
    "q",
    "specialty",
    "category",
    "city",
    "district",
    "district_without_city",
    "sort",
    "page",
    "available",
)

# Wildcard and escape characters go before transliteration (which drops them anyway).
_UNSAFE = str.maketrans("", "", "%_\\")
_TOKEN = re.compile(r"[^\W_]+")
# Reference slugs (catalog and places) share this shape; anything else cannot match a row and
# never reaches the database (a NUL byte there would be a driver error, not "not found").
_SLUG = re.compile(REF_SLUG_RE)
SLUG_MAX = 60


# --- parsing ----------------------------------------------------------------------------------


def parse_query(params: Mapping[str, str]) -> tuple[SearchQuery, list[str]]:
    """Clean URL parameters; return the query and codes of values that were ignored."""
    notices: list[str] = []

    def value(name: str) -> str | None:
        return (params.get(name) or "").strip() or None

    def slug(name: str, notice: str) -> str | None:
        found = value(name)
        if found is not None and (len(found) > SLUG_MAX or not _SLUG.fullmatch(found)):
            notices.append(notice)
            return None
        return found

    q = (params.get("q") or "").strip()[:MAX_QUERY].strip() or None
    if q is not None and not query_words(q):
        notices.append("q")  # kept for the search box, but nothing in it can be matched
    specialty = slug("s", "specialty")
    category = slug("cat", "category")
    city = slug("c", "city")
    if city is None and value("d") is not None:
        notices.append("district_without_city")  # district slugs are unique only per city
        district = None
    else:
        district = slug("d", "district")

    sort: Sort | None = None
    raw_sort = value("sort")
    if raw_sort is not None:
        if raw_sort in SORTS:
            sort = raw_sort  # type: ignore[assignment]
        else:
            notices.append("sort")

    page = 1
    raw_page = value("page")
    if raw_page is not None:
        try:
            page = int(raw_page)
        except ValueError:
            page = 0
        if not 1 <= page <= MAX_PAGE:
            notices.append("page")
            page = MAX_PAGE if page > MAX_PAGE else 1

    if value("available") not in (None, "1"):
        notices.append("available")  # only available=1 means something; anything else is dropped

    query = SearchQuery(
        q=q,
        specialty=specialty,
        category=category,
        city=city,
        district=district,
        available=value("available") == "1",
        sort=sort,
        page=page,
    )
    return query, notices


def query_words(q: str | None) -> list[str]:
    """At most 6 distinct transliterated words of 2+ characters."""
    if not q:
        return []
    words = translit(q[:MAX_QUERY].translate(_UNSAFE)).split()
    return list(dict.fromkeys(w for w in words if len(w) >= MIN_WORD))[:MAX_WORDS]


# --- highlighting -----------------------------------------------------------------------------


def _within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > 1:
            return False
        previous = current
    return previous[-1] <= 1


def _token_matches(token: str, words: Sequence[str]) -> bool:
    latin = translit(token)
    if not latin:
        return False
    for word in words:
        if word in latin:
            return True
        # Typos: the word is one edit away from the start of the token (endings vary).
        if len(word) >= 5 and any(
            _within_one_edit(word, latin[:k])
            for k in (len(word) - 1, len(word), len(word) + 1)
            if k <= len(latin)
        ):
            return True
    return False


def highlight(text: str, words: list[str]) -> list[Segment]:
    """Split a display string into (text, is_match) segments; never returns markup."""
    segments: list[Segment] = []

    def push(part: str, match: bool) -> None:
        if segments and segments[-1][1] == match:
            segments[-1] = (segments[-1][0] + part, match)
        else:
            segments.append((part, match))

    pos = 0
    for found in _TOKEN.finditer(text):
        if found.start() > pos:
            push(text[pos : found.start()], False)
        push(found.group(), bool(words) and _token_matches(found.group(), words))
        pos = found.end()
    if pos < len(text):
        push(text[pos:], False)
    return segments


# --- visibility and counts --------------------------------------------------------------------


def _visible(stmt: Select[Any]) -> Select[Any]:
    """Restrict to visible masters whose owner is active right now (defence in depth)."""
    return (
        stmt.join(MasterProfile, MasterProfile.id == MasterSearch.master_id)
        .join(User, User.id == MasterProfile.user_id)
        .where(
            MasterSearch.is_visible,
            MasterProfile.published_at.is_not(None),
            MasterProfile.hidden_at.is_(None),
            User.blocked_at.is_(None),
            User.deleted_at.is_(None),
        )
    )


def count_visible(
    db: Session,
    *,
    specialty_id: int,
    city_id: int,
    district_id: int | None = None,
    named_district_only: bool = False,
) -> int:
    """Visible masters of a specialty in a city, or in a district (named, or also whole-city)."""
    stmt = _visible(select(func.count()).select_from(MasterSearch)).where(
        MasterSearch.specialty_ids.contains([specialty_id])
    )
    if district_id is None:
        stmt = stmt.where(MasterSearch.city_ids.contains([city_id]))
    elif named_district_only:
        stmt = stmt.where(MasterSearch.named_district_ids.contains([district_id]))
    else:
        stmt = stmt.where(
            or_(
                MasterSearch.district_ids.contains([district_id]),
                MasterSearch.whole_city_ids.contains([city_id]),
            )
        )
    return db.scalar(stmt) or 0


def _count_pairs(db: Session, places: Select[Any]) -> dict[tuple[int, int], int]:
    """`places` yields (master_id, place_id, specialty_ids); count masters per place/specialty."""
    rows = places.subquery()
    pairs = select(
        rows.c.master_id,
        rows.c.place_id,
        func.unnest(rows.c.specialty_ids).label("specialty_id"),
    ).subquery()
    stmt = select(
        pairs.c.place_id, pairs.c.specialty_id, func.count(distinct(pairs.c.master_id))
    ).group_by(pairs.c.place_id, pairs.c.specialty_id)
    return {(place, specialty): n for place, specialty, n in db.execute(stmt)}


def counts_by_city_specialty(db: Session) -> dict[tuple[int, int], int]:
    """(city_id, specialty_id) → visible masters serving the city or any of its districts."""
    places = _visible(
        select(
            MasterSearch.master_id,
            func.unnest(MasterSearch.city_ids).label("place_id"),
            MasterSearch.specialty_ids,
        ).select_from(MasterSearch)
    )
    return _count_pairs(db, places)


def counts_by_district_specialty(db: Session, *, named_only: bool) -> dict[tuple[int, int], int]:
    """(district_id, specialty_id) → visible masters who named the district, or (not
    `named_only`) who named it or serve its whole city."""
    if named_only:
        places = select(
            MasterSearch.master_id,
            func.unnest(MasterSearch.named_district_ids).label("place_id"),
            MasterSearch.specialty_ids,
        ).select_from(MasterSearch)
    else:
        places = (
            select(
                MasterSearch.master_id, District.id.label("place_id"), MasterSearch.specialty_ids
            )
            .select_from(MasterSearch)
            .join(
                District,
                or_(
                    District.id == any_(MasterSearch.district_ids),
                    District.city_id == any_(MasterSearch.whole_city_ids),
                ),
            )
            .where(District.is_active)
        )
    return _count_pairs(db, _visible(places))


COUNTS_TTL = 600  # seconds; SEO pages and the sitemap read the grouped counts (spec D6)


def cached_counts_by_city_specialty(db: Session) -> dict[tuple[int, int], int]:
    """`counts_by_city_specialty`, at most ten minutes old. Callers must not modify the result."""
    return cache.get_or_compute(
        ("counts_by_city_specialty",), COUNTS_TTL, lambda: counts_by_city_specialty(db)
    )


def cached_counts_by_district_specialty(
    db: Session, *, named_only: bool
) -> dict[tuple[int, int], int]:
    """`counts_by_district_specialty`, at most ten minutes old. Do not modify the result."""
    return cache.get_or_compute(
        ("counts_by_district_specialty", named_only),
        COUNTS_TTL,
        lambda: counts_by_district_specialty(db, named_only=named_only),
    )


# --- similarity for hints and suggestions -----------------------------------------------------


def _trigrams(text: str) -> set[str]:
    """pg_trgm's trigrams of already transliterated text (each word padded "  w ")."""
    grams: set[str] = set()
    for word in text.split():
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


def _score(words: list[str], texts: Sequence[str]) -> float:
    """Best similarity of the whole query, or of any one word, to any of the texts."""
    probes = [_trigrams(" ".join(words)), *(_trigrams(w) for w in words)]
    targets = [_trigrams(translit(t)) for t in texts]
    return max((_similarity(p, t) for t in targets for p in probes), default=0.0)


@dataclass
class _Candidate:
    kind: str  # "specialty", "city" or "district"
    id: int
    slug: str
    name_ru: str
    name_tg: str
    texts: list[str]
    city_slug: str | None = None

    def name(self, lang: str) -> str:
        return (self.name_tg if lang == "tg" else self.name_ru) or self.name_ru


def _specialty_candidates(db: Session) -> list[_Candidate]:
    rows = db.execute(
        select(
            Specialty.id,
            Specialty.slug,
            Specialty.name_ru,
            Specialty.name_tg,
            SpecialtySynonym.text,
        )
        .join(Category, Category.id == Specialty.category_id)
        .outerjoin(SpecialtySynonym, SpecialtySynonym.specialty_id == Specialty.id)
        .where(Specialty.is_active, Category.is_active)
        .order_by(Specialty.id, SpecialtySynonym.id)
    ).all()
    found: dict[int, _Candidate] = {}
    for sid, slug, name_ru, name_tg, synonym in rows:
        item = found.setdefault(
            sid, _Candidate("specialty", sid, slug, name_ru, name_tg, [name_ru, name_tg])
        )
        if synonym:
            item.texts.append(synonym)
    return list(found.values())


def _place_candidates(db: Session) -> list[_Candidate]:
    synonyms: dict[tuple[str, int], list[str]] = defaultdict(list)
    for city_id, district_id, synonym in db.execute(
        select(PlaceSynonym.city_id, PlaceSynonym.district_id, PlaceSynonym.text).order_by(
            PlaceSynonym.id
        )
    ):
        key = ("city", city_id) if city_id is not None else ("district", district_id)
        synonyms[key].append(synonym)
    found = [
        _Candidate("city", c.id, c.slug, c.name_ru, c.name_tg, [c.name_ru, c.name_tg])
        for c in db.scalars(select(City).where(City.is_active).order_by(City.id))
    ]
    for district, city_slug in db.execute(
        select(District, City.slug)
        .join(City, City.id == District.city_id)
        .where(District.is_active, City.is_active)
        .order_by(District.id)
    ):
        found.append(
            _Candidate(
                "district",
                district.id,
                district.slug,
                district.name_ru,
                district.name_tg,
                [district.name_ru, district.name_tg],
                city_slug,
            )
        )
    for item in found:
        item.texts += synonyms.get((item.kind, item.id), [])
    return found


def _best(
    words: list[str], candidates: Sequence[_Candidate], threshold: float
) -> _Candidate | None:
    scored = [(_score(words, c.texts), i) for i, c in enumerate(candidates)]
    score, index = max(scored, key=lambda s: (s[0], -s[1]), default=(0.0, -1))
    return candidates[index] if score >= threshold else None


# --- search -----------------------------------------------------------------------------------


@dataclass
class _Filters:
    specialty_id: int | None = None
    category_id: int | None = None
    city_id: int | None = None
    district_id: int | None = None


def _resolve(db: Session, query: SearchQuery, notices: list[str]) -> _Filters:
    """Slugs to ids of active rows; an unknown slug is ignored with a notice."""
    found = _Filters()
    if query.specialty is not None:
        found.specialty_id = db.scalar(
            select(Specialty.id)
            .join(Category, Category.id == Specialty.category_id)
            .where(Specialty.slug == query.specialty, Specialty.is_active, Category.is_active)
        )
        if found.specialty_id is None:
            notices.append("specialty")
    if query.category is not None:
        found.category_id = db.scalar(
            select(Category.id).where(Category.slug == query.category, Category.is_active)
        )
        if found.category_id is None:
            notices.append("category")
    if query.city is not None:
        found.city_id = db.scalar(select(City.id).where(City.slug == query.city, City.is_active))
        if found.city_id is None:
            notices.append("city")
    if query.district is not None and found.city_id is not None:
        found.district_id = db.scalar(
            select(District.id).where(
                District.city_id == found.city_id,
                District.slug == query.district,
                District.is_active,
            )
        )
        if found.district_id is None:
            notices.append("district")
    return found


def _word(word: str) -> ColumnElement[str]:
    return bindparam(None, word, type_=Text)


def _conditions(filters: _Filters, words: list[str], available: bool) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if filters.specialty_id is not None:
        conds.append(MasterSearch.specialty_ids.contains([filters.specialty_id]))
    if filters.category_id is not None:
        conds.append(MasterSearch.category_ids.contains([filters.category_id]))
    if filters.district_id is not None and filters.city_id is not None:
        conds.append(
            or_(
                MasterSearch.district_ids.contains([filters.district_id]),
                MasterSearch.whole_city_ids.contains([filters.city_id]),
            )
        )
    elif filters.city_id is not None:
        conds.append(MasterSearch.city_ids.contains([filters.city_id]))
    if available:
        conds.append(MasterSearch.is_available)
    # Every word must match; the GIN trigram index serves `<%`.
    conds += [_word(w).op("<%", is_comparison=True)(MasterSearch.document) for w in words]
    return conds


def _order(sort: Sort, filters: _Filters, words: list[str]) -> list[ColumnElement[Any]]:
    if sort == "relevance":
        relevance: ColumnElement[Any] = sum(
            (func.word_similarity(_word(w), MasterSearch.document) for w in words),
            start=case((MasterSearch.has_photos, 0.1), else_=0.0),
        )
        relevance = relevance + case(
            (or_(*(_word(w).op("<%", is_comparison=True)(MasterSearch.names) for w in words)), 0.3),
            else_=0.0,
        )
        if filters.specialty_id is not None:
            relevance = relevance + case(
                (MasterSearch.primary_specialty_ids.contains([filters.specialty_id]), 0.2),
                else_=0.0,
            )
        return [relevance.desc(), MasterSearch.master_id]
    if sort == "newest":
        return [MasterSearch.published_at.desc().nulls_last(), MasterSearch.master_id]
    return [
        MasterSearch.rating_score.desc().nulls_last(),
        MasterSearch.has_photos.desc(),
        MasterSearch.published_at.desc().nulls_last(),
        MasterSearch.master_id,
    ]


def _pick(lang: str, name_ru: str, name_tg: str) -> str:
    return (name_tg if lang == "tg" else name_ru) or name_ru


def _cards(
    db: Session, rows: Sequence[Any], filters: _Filters, words: list[str], lang: str
) -> list[MasterCard]:
    """Cards for one page: specialties, areas and photo strips in one query each (no N+1)."""
    ids = [row.master_id for row in rows]
    specialties: dict[int, list[str]] = defaultdict(list)
    for master_id, name_ru, name_tg in db.execute(
        select(MasterSpecialty.master_id, Specialty.name_ru, Specialty.name_tg)
        .join(Specialty, Specialty.id == MasterSpecialty.specialty_id)
        .join(Category, Category.id == Specialty.category_id)
        .where(MasterSpecialty.master_id.in_(ids))
        .order_by(
            MasterSpecialty.master_id,
            MasterSpecialty.is_primary.desc(),
            Category.sort_order,
            Specialty.sort_order,
            Specialty.id,
        )
    ):
        specialties[master_id].append(_pick(lang, name_ru, name_tg))

    areas: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for master_id, city_id, city_ru, city_tg, district_id, district_ru, district_tg in db.execute(
        select(
            MasterArea.master_id,
            City.id,
            City.name_ru,
            City.name_tg,
            District.id,
            District.name_ru,
            District.name_tg,
        )
        .join(City, City.id == MasterArea.city_id)
        .outerjoin(District, District.id == MasterArea.district_id)
        .where(MasterArea.master_id.in_(ids))
        .order_by(
            MasterArea.master_id,
            City.sort_order,
            City.id,
            District.id.is_not(None),
            District.sort_order,
            District.id,
        )
    ):
        label = _pick(lang, city_ru, city_tg)
        if district_id is not None:
            label = f"{label} — {_pick(lang, district_ru, district_tg)}"
        # The area the visitor filtered by comes first.
        rank = 2
        if district_id is not None and district_id == filters.district_id:
            rank = 0
        elif city_id == filters.city_id:
            rank = 1
        areas[master_id].append((rank, label))

    position = func.row_number().over(
        partition_by=PortfolioCollection.master_id,
        order_by=(
            PortfolioCollection.position,
            PortfolioCollection.id,
            PortfolioPhoto.position,
            PortfolioPhoto.id,
        ),
    )
    strip = (
        select(PortfolioCollection.master_id, PortfolioPhoto.thumb_key, position.label("n"))
        .join(PortfolioPhoto, PortfolioPhoto.collection_id == PortfolioCollection.id)
        .where(
            PortfolioCollection.master_id.in_(ids),
            PortfolioCollection.hidden_at.is_(None),
            PortfolioPhoto.deleted_at.is_(None),
            PortfolioPhoto.hidden_at.is_(None),
        )
        .subquery()
    )
    storage = get_storage()
    photos: dict[int, list[str]] = defaultdict(list)
    for master_id, thumb_key in db.execute(
        select(strip.c.master_id, strip.c.thumb_key)
        .where(strip.c.n <= CARD_PHOTOS)
        .order_by(strip.c.master_id, strip.c.n)
    ):
        photos[master_id].append(storage.url(thumb_key))

    cards = []
    for row in rows:
        own_areas = sorted(areas[row.master_id], key=lambda a: a[0])  # stable: keeps the order
        area = own_areas[0][1] if own_areas else ""
        if len(own_areas) > 1:
            area = f"{area} +{len(own_areas) - 1}"
        cards.append(
            MasterCard(
                slug=row.slug,
                display_name=highlight(row.display_name, words),
                headline=highlight(row.headline or "", words),
                avatar_url=storage.url(row.avatar_key) if row.avatar_key else None,
                specialties=[
                    highlight(name, words) for name in specialties[row.master_id][:CARD_SPECIALTIES]
                ],
                area=area,
                rating=float(row.rating_avg) if row.rating_avg is not None else None,
                photos=photos[row.master_id],
                is_available=row.is_available,
            )
        )
    return cards


def _run(
    db: Session, query: SearchQuery, words: list[str], lang: str, notices: list[str]
) -> SearchResult:
    filters = _resolve(db, query, notices)
    conds = _conditions(filters, words, query.available)
    total = db.scalar(_visible(select(func.count()).select_from(MasterSearch)).where(*conds)) or 0
    pages = min(math.ceil(total / PAGE_SIZE), MAX_PAGE)
    page = max(min(query.page, pages), 1)  # a page past the end shows the last one, with a notice
    if page != query.page and "page" not in notices:
        notices.append("page")
    sort: Sort = query.sort or ("relevance" if words else "rating")
    if sort == "relevance" and not words:
        sort = "rating"

    offset = (page - 1) * PAGE_SIZE
    items: list[MasterCard] = []
    if offset < total:
        rows = db.execute(
            _visible(
                select(
                    MasterSearch.master_id,
                    MasterProfile.slug,
                    MasterProfile.display_name,
                    MasterProfile.headline,
                    MasterProfile.avatar_key,
                    MasterProfile.rating_avg,
                    MasterSearch.is_available,
                ).select_from(MasterSearch)
            )
            .where(*conds)
            .order_by(*_order(sort, filters, words))
            .limit(PAGE_SIZE)
            .offset(offset)
        ).all()
        items = _cards(db, rows, filters, words, lang)

    hint = suggestion = None
    if words:
        specialties = _specialty_candidates(db)
        # A chip that narrows an empty result to a specialty leads nowhere; "did you mean" does.
        if filters.specialty_id is None and total > 0:
            best = _best(words, specialties, HINT_THRESHOLD)
            if best is not None:
                hint = SpecialtyHint(slug=best.slug, name=best.name(lang))
        if total == 0:
            applied = {
                ("specialty", filters.specialty_id),
                ("city", filters.city_id),
                ("district", filters.district_id),
            }
            pool = [
                c for c in [*specialties, *_place_candidates(db)] if (c.kind, c.id) not in applied
            ]
            best = _best(words, pool, SUGGEST_THRESHOLD)
            if best is not None:
                suggestion = Suggestion(
                    kind=best.kind,  # type: ignore[arg-type]
                    slug=best.slug,
                    name=best.name(lang),
                    city_slug=best.city_slug,
                )
    return SearchResult(
        items=items,
        total=total,
        page=page,
        pages=pages,
        suggestion=suggestion,
        specialty_hint=hint,
        notices=notices,
    )


def search(db: Session, query: SearchQuery, *, lang: str) -> SearchResult:
    """One page of visible masters. Read-only: its settings live in a savepoint rolled back at
    the end, so the 2 s timeout and the trigram threshold never reach the caller's queries."""
    words = query_words(query.q)
    notices: list[str] = []
    savepoint = db.begin_nested()
    try:
        db.execute(
            select(
                func.set_config("pg_trgm.word_similarity_threshold", str(WORD_THRESHOLD), True),
                func.set_config("statement_timeout", STATEMENT_TIMEOUT, True),
            )
        )
        return _run(db, query, words, lang, notices)
    except OperationalError as exc:
        if not isinstance(exc.orig, QueryCanceled):
            raise
        return SearchResult(
            items=[], total=0, page=query.page, pages=0, timed_out=True, notices=notices
        )
    finally:
        savepoint.rollback()
