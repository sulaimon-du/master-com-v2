"""Search service (spec D2, D3, D9, D11): filters, typo and script tolerance, hints, safety."""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import (
    City,
    District,
    MasterProfile,
    PortfolioCollection,
    PortfolioPhoto,
    Specialty,
    User,
)
from app.schemas.search import SearchQuery
from app.services import masters, search
from app.services.masters import BasicsInput
from app.services.search import (
    count_visible,
    counts_by_city_specialty,
    counts_by_district_specialty,
    highlight,
    parse_query,
)
from app.services.search_index import refresh_search
from app.services.storage import new_key


@pytest.fixture(autouse=True)
def empty_index(db: Session) -> None:
    """Only the masters a test creates are in the index (rolled back with the test)."""
    db.execute(text("DELETE FROM master_search"))


def _spec(db: Session, slug: str) -> Specialty:
    return db.scalars(select(Specialty).where(Specialty.slug == slug)).one()


def _city(db: Session, slug: str) -> City:
    return db.scalars(select(City).where(City.slug == slug)).one()


def _district(db: Session, city: str, slug: str) -> District:
    return db.scalars(
        select(District).join(City).where(City.slug == city, District.slug == slug)
    ).one()


def _master(
    db: Session,
    name: str = "Иван Петров",
    *,
    headline: str | None = None,
    bio: str | None = None,
    specs: tuple[str, ...] = ("santekhnik",),
    primary: tuple[str, ...] | None = None,
    cities: tuple[str, ...] = ("dushanbe",),
    districts: tuple[tuple[str, str], ...] = (),
    available: bool = True,
    ready: bool = True,
) -> MasterProfile:
    """A master made through the masters service, published when `ready`."""
    n = uuid.uuid4().hex[:10]
    user = User(email=f"{n}@example.com", name="Иван")
    user.phone = "+99290" + str(int(n, 16))[:7].ljust(7, "0")
    user.phone_verified_at = utcnow()
    db.add(user)
    db.flush()
    data = BasicsInput(
        display_name=name,
        slug=f"m-{n}",
        headline=headline,
        experience_years=3,
        is_available=available,
    )
    master = masters.create_profile(db, user, data)
    if bio is not None:
        masters.update_basics(db, master, data, bio=bio)
    if ready:
        spec_ids = [_spec(db, s).id for s in specs]
        primary_ids = [_spec(db, s).id for s in (specs[:1] if primary is None else primary)]
        masters.set_specialties(db, master, spec_ids, primary_ids)
        masters.set_areas(
            db,
            master,
            [_city(db, c).id for c in cities],
            [_district(db, c, d).id for c, d in districts],
        )
    return master


def _set(db: Session, master: MasterProfile, **values) -> None:  # type: ignore[no-untyped-def]
    for key, value in values.items():
        setattr(master, key, value)
    db.flush()
    refresh_search(db, master)


def _photos(db: Session, master: MasterProfile, count: int, *, hidden: bool = False) -> None:
    col = PortfolioCollection(master_id=master.id, title="Работы", position=0)
    if hidden:
        col.hidden_at = utcnow()
    db.add(col)
    db.flush()
    for i in range(count):
        key = new_key("p")
        db.add(
            PortfolioPhoto(
                collection_id=col.id,
                storage_key=key,
                thumb_key=new_key("p", thumb_of=key),
                width=10,
                height=10,
                position=i,
            )
        )
    db.flush()
    refresh_search(db, master)


def _q(**params: str) -> SearchQuery:
    query, _ = parse_query(params)
    return query


def _slugs(db: Session, lang: str = "ru", **params: str) -> list[str]:
    return [card.slug for card in search.search(db, _q(**params), lang=lang).items]


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    seen: list[str] = []
    engine = db.get_bind().engine

    def listen(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", listen)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", listen)


# --- parsing ----------------------------------------------------------------------------------


def test_parse_query_defaults_and_notices() -> None:
    query, notices = parse_query({})
    assert query == SearchQuery(
        q=None,
        specialty=None,
        category=None,
        city=None,
        district=None,
        available=False,
        sort=None,
        page=1,
    )
    assert notices == []
    query, notices = parse_query(
        {"q": "  сантехник  ", "s": "santekhnik", "available": "1", "sort": "newest", "page": "3"}
    )
    assert (query.q, query.specialty, query.available, query.sort, query.page) == (
        "сантехник",
        "santekhnik",
        True,
        "newest",
        3,
    )
    assert notices == []
    _, notices = parse_query({"sort": "price", "page": "x"})
    assert set(notices) == {"sort", "page"}


def test_district_without_city_is_ignored_with_a_notice(db: Session) -> None:
    whole = _master(db)
    named = _master(db, cities=(), districts=(("dushanbe", "sino"),))
    other = _master(db, cities=(), districts=(("dushanbe", "firdavsi"),))
    query, notices = parse_query({"d": "sino"})
    assert query.district is None
    assert "district_without_city" in notices
    assert set(_slugs(db, d="sino")) == {whole.slug, named.slug, other.slug}


# --- filters ----------------------------------------------------------------------------------


def test_filters_each_alone_and_combined(db: Session) -> None:
    a = _master(db, specs=("santekhnik",), cities=("dushanbe",))
    b = _master(db, specs=("elektrik",), cities=("khujand",))
    c = _master(db, specs=("santekhnik",), cities=("khujand",), available=False)
    assert set(_slugs(db)) == {a.slug, b.slug, c.slug}
    assert set(_slugs(db, s="santekhnik")) == {a.slug, c.slug}
    assert set(_slugs(db, c="khujand")) == {b.slug, c.slug}
    assert set(_slugs(db, s="santekhnik", c="khujand")) == {c.slug}
    assert set(_slugs(db, available="1")) == {a.slug, b.slug}
    assert set(_slugs(db, s="santekhnik", c="khujand", available="1")) == set()
    assert set(_slugs(db, q="сантехник", c="dushanbe")) == {a.slug}


def test_category_filter(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",))
    pipes = _master(db, specs=("vodoprovod",))
    _master(db, specs=("elektrik",))
    assert set(_slugs(db, cat="santekhnika")) == {plumber.slug, pipes.slug}


def test_district_matches_named_district_or_whole_city(db: Session) -> None:
    whole = _master(db, cities=("dushanbe",))
    sino = _master(db, cities=(), districts=(("dushanbe", "sino"),))
    firdavsi = _master(db, cities=(), districts=(("dushanbe", "firdavsi"),))
    _master(db, cities=("khujand",))
    assert set(_slugs(db, c="dushanbe", d="sino")) == {whole.slug, sino.slug}
    assert set(_slugs(db, c="dushanbe")) == {whole.slug, sino.slug, firdavsi.slug}


def test_unknown_slugs_are_ignored_with_notices(db: Session) -> None:
    a = _master(db)
    result = search.search(db, _q(s="nope", cat="nope", c="nope", d="nope"), lang="ru")
    assert [card.slug for card in result.items] == [a.slug]
    assert set(result.notices) >= {"specialty", "category", "city"}
    result = search.search(db, _q(c="dushanbe", d="nope"), lang="ru")
    assert [card.slug for card in result.items] == [a.slug]
    assert "district" in result.notices


# --- sorting and pagination -------------------------------------------------------------------


def test_sort_orders_and_tie_breaks(db: Session) -> None:
    now = utcnow()
    low = _master(db)
    high = _master(db)
    unrated = _master(db)
    tie_a = _master(db)
    tie_b = _master(db)
    _set(db, low, rating_score=Decimal("3.000"), published_at=now - timedelta(days=1))
    _set(db, high, rating_score=Decimal("4.900"), published_at=now - timedelta(days=5))
    _set(db, unrated, rating_score=None, published_at=now)
    _set(db, tie_a, rating_score=Decimal("4.000"), published_at=now - timedelta(days=3))
    _set(db, tie_b, rating_score=Decimal("4.000"), published_at=now - timedelta(days=3))
    _photos(db, tie_b, 1)
    first_tie, second_tie = sorted([tie_a, tie_b], key=lambda m: m.id)

    # rating: score desc nulls last, then has_photos, then newest, then id
    assert _slugs(db) == [high.slug, tie_b.slug, tie_a.slug, low.slug, unrated.slug]
    assert _slugs(db, sort="rating") == _slugs(db)
    # newest: published_at desc, ties by id
    assert _slugs(db, sort="newest") == [
        unrated.slug,
        low.slug,
        first_tie.slug,
        second_tie.slug,
        high.slug,
    ]
    # relevance without a query falls back to rating
    assert _slugs(db, sort="relevance") == _slugs(db)


def test_relevance_is_default_with_a_query_and_ties_break_by_id(db: Session) -> None:
    one = _master(db, "Ахмад")
    two = _master(db, "Бахтиёр")
    named = _master(db, "Сантехник Карим", specs=("elektrik",), bio="сантехник")
    result = _slugs(db, q="сантехник")
    assert result[0] == named.slug  # name match bonus
    assert one.id < two.id
    assert result[1:] == [one.slug, two.slug]


def test_pagination_bounds(db: Session) -> None:
    created = [_master(db) for _ in range(21)]
    first = search.search(db, _q(), lang="ru")
    assert (first.total, first.page, first.pages, len(first.items)) == (21, 1, 2, 20)
    second = search.search(db, _q(page="2"), lang="ru")
    assert len(second.items) == 1
    assert {c.slug for c in first.items + second.items} == {m.slug for m in created}
    beyond = search.search(db, _q(page="3"), lang="ru")
    assert beyond.items == [] and beyond.total == 21
    for bad, expected in (("0", 1), ("-4", 1), ("abc", 1), ("51", 50), ("99999999999", 50)):
        query, notices = parse_query({"page": bad})
        assert query.page == expected and notices == ["page"]


# --- free text ----------------------------------------------------------------------------------


def test_transliteration_and_tajik_letters(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",), cities=("dushanbe",))
    bokhtar = _master(db, specs=("elektrik",), cities=("bokhtar",))
    assert _slugs(db, q="santehnik") == [plumber.slug]
    assert _slugs(db, q="қубуркаш") == [plumber.slug]
    assert _slugs(db, q="Курган-Тюбе") == [bokhtar.slug]
    assert _slugs(db, q="kurgan tyube") == [bokhtar.slug]


def test_typo_finds_the_specialty(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",))
    _master(db, specs=("elektrik",))
    assert _slugs(db, q="сантехнк") == [plumber.slug]


def test_synonym_finds_the_specialty(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",))
    _master(db, specs=("elektrik",))
    assert _slugs(db, q="смеситель") == [plumber.slug]


def test_every_word_must_match(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",), cities=("dushanbe",))
    _master(db, specs=("santekhnik",), cities=("khujand",))
    _master(db, specs=("elektrik",), cities=("dushanbe",))
    assert _slugs(db, q="сантехник душанбе") == [plumber.slug]


def test_specialty_hint_for_a_query_that_names_a_specialty(db: Session) -> None:
    plumber = _master(db, specs=("santekhnik",))
    electrician = _master(db, "Карим", specs=("elektrik",), bio="Также сантехник")
    result = search.search(db, _q(q="сантехник"), lang="ru")
    assert result.specialty_hint is not None
    assert result.specialty_hint.slug == "santekhnik"
    assert result.specialty_hint.name == "Сантехник"
    # a hint, not a filter: the electrician who mentions it is still listed
    assert {c.slug for c in result.items} == {plumber.slug, electrician.slug}
    tg = search.search(db, _q(q="santehnik"), lang="tg").specialty_hint
    assert tg is not None and tg.slug == "santekhnik"
    assert tg.name == _spec(db, "santekhnik").name_tg
    assert search.search(db, _q(q="сантехник", s="santekhnik"), lang="ru").specialty_hint is None
    assert search.search(db, _q(q="Иван"), lang="ru").specialty_hint is None


def test_did_you_mean_only_without_results(db: Session) -> None:
    _master(db, specs=("santekhnik",), cities=("dushanbe",))
    result = search.search(db, _q(q="электрик"), lang="ru")
    assert result.total == 0
    assert result.suggestion is not None
    assert (result.suggestion.kind, result.suggestion.slug) == ("specialty", "elektrik")
    assert result.suggestion.name == "Электрик"
    place = search.search(db, _q(q="чкаловск"), lang="ru")  # old name of Buston
    assert place.total == 0
    assert place.suggestion is not None
    assert (place.suggestion.kind, place.suggestion.slug) == ("city", "buston")
    assert place.suggestion.name == "Бустон"
    typo = search.search(db, _q(q="Худжандд"), lang="tg")
    assert typo.suggestion is not None and typo.suggestion.slug == "khujand"
    assert typo.suggestion.name == _city(db, "khujand").name_tg
    found = search.search(db, _q(q="сантехник"), lang="ru")
    assert found.total == 1 and found.suggestion is None
    assert search.search(db, _q(q="zzqqxx"), lang="ru").suggestion is None


HOSTILE = [
    "%",
    "_",
    "%%%%_%%",
    "\\",
    "'; DROP TABLE users; --",
    "' OR 1=1 --",
    ".*(a+)+$",
    "[[[(((",
    "ж" * 10_000,
    "a" * 10_000,
    "🔧🚿🔧",
    "      ",
    "\t\n\r",
    "\x00",
    "сантехник%",
    "santeh_nik",
]


@pytest.mark.parametrize("q", HOSTILE)
def test_hostile_free_text(db: Session, statements: list[str], q: str) -> None:
    plumber = _master(db)
    statements.clear()
    query, _ = parse_query({"q": q})
    assert query.q is None or len(query.q) <= 100
    result = search.search(db, query, lang="ru")
    assert result.timed_out is False
    assert result.total == len(result.items) <= 1
    if q.strip(" \t\n\r\x00%_\\") == "" or q == "🔧🚿🔧":
        assert [c.slug for c in result.items] == [plumber.slug]  # no words: plain listing
    if q.startswith("сантехник") or q == "santeh_nik":
        assert [c.slug for c in result.items] == [plumber.slug]
    assert not any(" like " in s.lower() or "~" in s for s in statements)
    db.execute(text("SELECT 1"))  # the session is still usable


@pytest.mark.parametrize("bad", ["\x00", "santekhnik\x00", "../etc", "Сантехник", "a" * 10_000])
def test_hostile_slug_params_are_ignored_with_notices(db: Session, bad: str) -> None:
    plumber = _master(db)
    query, notices = parse_query({"s": bad, "cat": bad, "c": bad, "d": bad})
    assert (query.specialty, query.category, query.city, query.district) == (None,) * 4
    assert set(notices) == {"specialty", "category", "city", "district_without_city"}
    assert [c.slug for c in search.search(db, query, lang="ru").items] == [plumber.slug]
    query, notices = parse_query({"c": "dushanbe", "d": bad})
    assert (query.city, query.district, notices) == ("dushanbe", None, ["district"])


def test_query_without_usable_words_lists_all_with_a_notice() -> None:
    for q in ("%", "🔧", "a", "ь", "_ \\"):
        query, notices = parse_query({"q": q})
        assert query.q == q and notices == ["q"]
    assert parse_query({"q": "   "}) == parse_query({})
    assert parse_query({"q": "иван"})[1] == []


def test_no_specialty_hint_without_results(db: Session) -> None:
    _master(db, specs=("santekhnik",))
    result = search.search(db, _q(q="электрик"), lang="ru")
    assert result.total == 0 and result.specialty_hint is None
    assert result.suggestion is not None and result.suggestion.slug == "elektrik"


def test_blocked_by_sql_and_draft_never_listed(db: Session) -> None:
    blocked = _master(db)
    deleted = _master(db)
    _master(db, ready=False)  # draft
    kept = _master(db)
    db.execute(text("UPDATE users SET blocked_at = now() WHERE id = :id"), {"id": blocked.user_id})
    db.execute(text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": deleted.user_id})
    db.expire_all()
    city = _city(db, "dushanbe")
    spec = _spec(db, "santekhnik")
    for params in ({}, {"q": "иван"}, {"s": "santekhnik", "c": "dushanbe"}):
        result = search.search(db, _q(**params), lang="ru")
        assert [c.slug for c in result.items] == [kept.slug]
        assert result.total == 1
    assert count_visible(db, specialty_id=spec.id, city_id=city.id) == 1
    assert counts_by_city_specialty(db) == {(city.id, spec.id): 1}


# --- cards and highlighting -------------------------------------------------------------------


def test_highlight_segments_are_correct_and_html_free() -> None:
    assert highlight("Иван Петров", ["ivan"]) == [("Иван", True), (" Петров", False)]
    assert highlight("Сантехник", ["santehnik"]) == [("Сантехник", True)]  # edit distance 1
    assert highlight("Сантехника и трубы", ["santekhnik"]) == [
        ("Сантехника", True),
        (" и трубы", False),
    ]
    assert highlight("Қубурсоз", ["qubur"]) == [("Қубурсоз", True)]
    assert highlight("Иван", []) == [("Иван", False)]
    assert highlight("", ["ivan"]) == []
    hostile = "<b>Иван</b> <script>alert(1)</script>"
    segments = highlight(hostile, ["ivan"])
    assert "".join(t for t, _ in segments) == hostile
    assert ("Иван", True) in segments
    assert all(isinstance(t, str) and isinstance(m, bool) for t, m in segments)
    assert highlight("Плиточник", ["plotnik"]) == [("Плиточник", False)]  # too far


def test_card_fields(db: Session) -> None:
    master = _master(
        db,
        "Иван Петров",
        headline="Сантехник с опытом",
        specs=("vodoprovod", "elektrik", "santekhnik", "kanalizatsiya"),
        primary=("santekhnik",),
        cities=(),
        districts=(("dushanbe", "sino"), ("dushanbe", "firdavsi")),
    )
    _set(db, master, rating_avg=Decimal("4.80"), avatar_key=new_key("a"))
    _photos(db, master, 6)
    _photos(db, master, 2, hidden=True)
    result = search.search(db, _q(q="сантехник"), lang="ru")
    card = result.items[0]
    assert card.slug == master.slug
    assert card.display_name == [("Иван Петров", False)]
    assert card.headline == [("Сантехник", True), (" с опытом", False)]
    assert card.avatar_url == f"/media/{master.avatar_key}"
    assert len(card.specialties) == 3
    assert card.specialties[0] == [("Сантехник", True)]
    assert card.rating == 4.8
    assert card.is_available is True
    assert len(card.photos) == 4 and all(p.endswith("-t.webp") for p in card.photos)
    assert card.area.startswith("Душанбе — ") and card.area.endswith("+1")
    tg = search.search(db, _q(), lang="tg").items[0]
    assert tg.specialties[0] == [(_spec(db, "santekhnik").name_tg, False)]
    assert tg.area.startswith(_city(db, "dushanbe").name_tg)
    # with a district filter, that district is the one shown
    firdavsi = search.search(db, _q(c="dushanbe", d="firdavsi"), lang="ru").items[0]
    assert firdavsi.area == "Душанбе — Фирдавси +1"


def test_statement_count_does_not_grow_with_results(db: Session, statements: list[str]) -> None:
    def count(params: dict[str, str]) -> tuple[int, int]:
        statements.clear()
        result = search.search(db, _q(**params), lang="ru")
        return len(result.items), len(statements)

    lone = _master(db, "Ёрмахмад Одиночка", specs=("pechnik",))
    _photos(db, lone, 5)
    for _ in range(25):
        m = _master(db, specs=("santekhnik", "elektrik"), cities=("dushanbe", "khujand"))
        _photos(db, m, 5)
    one_items, one_statements = count({"s": "pechnik"})
    many_items, many_statements = count({"s": "santekhnik"})
    assert (one_items, many_items) == (1, 20)
    assert many_statements == one_statements
    one_items, one_statements = count({"q": "ёрмахмад"})
    many_items, many_statements = count({"q": "иван"})
    assert (one_items, many_items) == (1, 20)
    assert many_statements == one_statements


def test_timeout_returns_timed_out_and_keeps_the_session(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _master(db)
    monkeypatch.setattr(search, "STATEMENT_TIMEOUT", "50ms")
    engine = db.get_bind().engine

    def slow(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if "master_search" in statement and "count(" in statement:
            return "SELECT pg_sleep(1)", {}
        return statement, parameters

    event.listen(engine, "before_cursor_execute", slow, retval=True)
    try:
        result = search.search(db, _q(q="сантехник"), lang="ru")
    finally:
        event.remove(engine, "before_cursor_execute", slow)
    assert result.timed_out is True
    assert result.items == [] and result.total == 0
    assert db.scalar(text("SELECT 1")) == 1
    # the timeout does not outlive the search
    assert db.scalar(text("SHOW statement_timeout")) != "50ms"
    assert search.search(db, _q(q="сантехник"), lang="ru").total == 1


def test_search_settings_do_not_leak_into_the_transaction(db: Session) -> None:
    db.execute(text("SELECT similarity('a', 'a')"))  # loads pg_trgm and its settings
    before = db.scalar(text("SHOW pg_trgm.word_similarity_threshold"))
    _master(db)
    search.search(db, _q(q="сантехник"), lang="ru")
    assert db.scalar(text("SHOW pg_trgm.word_similarity_threshold")) == before
    assert db.scalar(text("SHOW statement_timeout")) == "0"


# --- counts for SEO pages ---------------------------------------------------------------------


def test_counts_for_seo_pages(db: Session) -> None:
    dushanbe = _city(db, "dushanbe")
    sino = _district(db, "dushanbe", "sino")
    plumber = _spec(db, "santekhnik")
    electrician = _spec(db, "elektrik")
    _master(db, specs=("santekhnik",), cities=("dushanbe",))
    _master(db, specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    _master(db, specs=("santekhnik", "elektrik"), cities=(), districts=(("dushanbe", "sino"),))
    _master(db, specs=("elektrik",), cities=("khujand",))
    assert count_visible(db, specialty_id=plumber.id, city_id=dushanbe.id) == 3
    assert count_visible(db, specialty_id=plumber.id, city_id=dushanbe.id, district_id=sino.id) == 3
    assert (
        count_visible(
            db,
            specialty_id=plumber.id,
            city_id=dushanbe.id,
            district_id=sino.id,
            named_district_only=True,
        )
        == 2
    )
    by_city = counts_by_city_specialty(db)
    assert by_city[(dushanbe.id, plumber.id)] == 3
    assert by_city[(dushanbe.id, electrician.id)] == 1
    assert by_city[(_city(db, "khujand").id, electrician.id)] == 1
    named = counts_by_district_specialty(db, named_only=True)
    assert named == {(sino.id, plumber.id): 2, (sino.id, electrician.id): 1}
    listed = counts_by_district_specialty(db, named_only=False)
    assert listed[(sino.id, plumber.id)] == 3
    firdavsi = _district(db, "dushanbe", "firdavsi")
    assert listed[(firdavsi.id, plumber.id)] == 1
    assert (firdavsi.id, electrician.id) not in listed


# --- review fold-ins (task 4) -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "true", "yes", "2"])
def test_available_other_than_one_is_ignored_with_a_notice(db: Session, value: str) -> None:
    a = _master(db, available=True)
    b = _master(db, available=False)
    query, notices = parse_query({"available": value})
    assert query.available is False
    assert notices == ["available"]
    assert set(_slugs(db, available=value)) == {a.slug, b.slug}
    assert parse_query({"available": "1"})[1] == []
    assert parse_query({"available": ""})[1] == []


def test_card_availability_comes_from_the_search_row(db: Session) -> None:
    master = _master(db, available=True)
    row = db.get(search.MasterSearch, master.id)
    assert row is not None
    row.is_available = False  # the index says unavailable; the profile row still says available
    db.flush()
    [card] = search.search(db, _q(), lang="ru").items
    assert card.is_available is False
    assert _slugs(db, available="1") == []


def test_every_notice_code_is_documented() -> None:
    """The set of codes `parse_query` and the search can emit is `search.NOTICE_CODES`."""
    hostile = {
        "q": "%%",
        "s": "Bad Slug",
        "cat": "x" * 80,
        "c": "UPPER",
        "d": "district",
        "sort": "price",
        "page": "x",
        "available": "0",
    }
    emitted = set(parse_query(hostile)[1]) | set(parse_query({"d": "x"})[1])
    assert emitted | {"specialty", "category", "city", "district"} == set(search.NOTICE_CODES)
    assert "district_without_city" in emitted and "q" in emitted
