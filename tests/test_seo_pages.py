"""SEO landing pages in ru and tg (spec D4, D5, D7, D11.1, D11.2, D11.4; Review Focus 3)."""

import re
from collections.abc import Iterator
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.time import utcnow
from app.models import Category, City, District, Specialty, User
from app.seed import load
from app.services import cache
from app.services.search_index import refresh_search
from tests.test_search_service import _master

SITE = "https://ustoi.example"
ROBOTS_RE = re.compile(r'<meta name="robots" content="([^"]*)">')


@pytest.fixture
def web(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app on the test session (what a test creates is visible, then rolled back)."""
    monkeypatch.setenv("SITE_URL", SITE + "/")  # a trailing slash must not double up
    get_settings.cache_clear()
    db.execute(text("DELETE FROM master_search"))
    client.app.dependency_overrides[get_db] = lambda: db  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.pop(get_db, None)  # type: ignore[attr-defined]


def _robots(html: str) -> str:
    found = ROBOTS_RE.findall(html)
    assert len(found) == 1, found
    return found[0]


def _attr(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    assert match is not None, pattern
    return unescape(match.group(1))


def _canonical(html: str) -> str:
    return _attr(html, r'<link rel="canonical" href="([^"]*)">')


def _og(html: str, prop: str) -> str:
    return _attr(html, rf'<meta property="og:{prop}" content="([^"]*)">')


def _title(html: str) -> str:
    return _attr(html, r"<title>([^<]*)</title>")


def _section(html: str, section_id: str) -> str:
    """The HTML of one <section id=...> (empty when the page has none)."""
    match = re.search(rf'<section[^>]*id="{section_id}"[^>]*>(.*?)</section>', html, re.S)
    return match.group(1) if match else ""


def _masters(db: Session, n: int, **kwargs) -> list[str]:  # type: ignore[no-untyped-def]
    return [_master(db, f"Мастер {i}", **kwargs).slug for i in range(n)]


# --- listing and indexing ---------------------------------------------------------------------


def test_city_page_lists_real_masters_and_is_indexable_from_three(
    web: TestClient, db: Session
) -> None:
    slugs = _masters(db, 3, specs=("santekhnik",), cities=("dushanbe",))
    _master(db, "Чужой", specs=("elektrik",), cities=("dushanbe",))
    r = web.get("/dushanbe/santekhnik")
    assert r.status_code == 200
    assert '<html lang="ru">' in r.text
    for slug in slugs:
        assert f'href="/m/{slug}"' in r.text
    assert "Чужой" not in r.text
    assert _robots(r.text) == "index, follow"
    assert _canonical(r.text) == f"{SITE}/dushanbe/santekhnik"
    assert r.headers["cache-control"] == "no-store"


def test_fewer_than_three_masters_is_noindex(web: TestClient, db: Session) -> None:
    _masters(db, 2, specs=("santekhnik",), cities=("dushanbe",))
    r = web.get("/dushanbe/santekhnik")
    assert r.status_code == 200
    assert _robots(r.text) == "noindex, follow"


def test_hidden_masters_do_not_count(web: TestClient, db: Session) -> None:
    masters = [_master(db, f"Мастер {i}", specs=("santekhnik",)) for i in range(3)]
    db.execute(update(User).where(User.id == masters[0].user_id).values(blocked_at=utcnow()))
    r = web.get("/dushanbe/santekhnik")
    assert f'href="/m/{masters[0].slug}"' not in r.text
    assert _robots(r.text) == "noindex, follow"


def test_empty_page_is_200_noindex_with_a_call_to_post_a_request(web: TestClient) -> None:
    r = web.get("/dushanbe/santekhnik")
    assert r.status_code == 200
    assert _robots(r.text) == "noindex, follow"
    empty = _section(r.text, "seo-empty")
    assert 'href="/requests/new?c=dushanbe&amp;s=santekhnik"' in empty
    assert "seo." not in r.text  # every key has a text
    assert "{" not in _title(r.text)


def test_city_page_counts_masters_who_named_one_of_its_districts(
    web: TestClient, db: Session
) -> None:
    slugs = _masters(db, 3, specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    r = web.get("/dushanbe/santekhnik")
    for slug in slugs:
        assert f'href="/m/{slug}"' in r.text
    assert _robots(r.text) == "index, follow"


def test_district_page_lists_whole_city_but_counts_only_named_district(
    web: TestClient, db: Session
) -> None:
    whole = _masters(db, 3, specs=("santekhnik",), cities=("dushanbe",))
    named = _masters(db, 2, specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    elsewhere = _master(db, "Фирдавси", cities=(), districts=(("dushanbe", "firdavsi"),))
    r = web.get("/dushanbe/sino/santekhnik")
    assert r.status_code == 200
    for slug in whole + named:
        assert f'href="/m/{slug}"' in r.text
    assert f'href="/m/{elsewhere.slug}"' not in r.text
    assert _robots(r.text) == "noindex, follow"  # only 2 named Sino
    assert "5" in _title(r.text)  # the title counts everyone listed (D11.4)
    assert _canonical(r.text) == f"{SITE}/dushanbe/sino/santekhnik"

    _master(db, "Третий", cities=(), districts=(("dushanbe", "sino"),))
    assert _robots(web.get("/dushanbe/sino/santekhnik").text) == "index, follow"


def test_page_two_is_noindex_with_a_self_canonical(web: TestClient, db: Session) -> None:
    _masters(db, 23, specs=("santekhnik",), cities=("dushanbe",))
    first = web.get("/dushanbe/santekhnik").text
    assert _robots(first) == "index, follow"
    assert _canonical(first) == f"{SITE}/dushanbe/santekhnik"
    assert 'rel="next" href="/dushanbe/santekhnik?page=2"' in first
    assert "23" in _title(first)

    second = web.get("/dushanbe/santekhnik", params={"page": "2"}).text
    assert _robots(second) == "noindex, follow"
    assert _canonical(second) == f"{SITE}/dushanbe/santekhnik?page=2"
    assert _attr(second, r'hreflang="tg" href="([^"]*)"') == f"{SITE}/tg/dushanbe/santekhnik?page=2"
    assert 'rel="prev" href="/dushanbe/santekhnik"' in second

    # past the end: the search's clamping applies, without the search page's notice
    beyond = web.get("/dushanbe/santekhnik", params={"page": "99"})
    assert beyond.status_code == 200
    assert _canonical(beyond.text) == f"{SITE}/dushanbe/santekhnik?page=2"
    assert "data-notice" not in beyond.text
    # junk and page=1 are the first page with a clean canonical
    for junk in ("abc", "1", "-3"):
        page = web.get("/dushanbe/santekhnik", params={"page": junk}).text
        assert _canonical(page) == f"{SITE}/dushanbe/santekhnik"
        assert _robots(page) == "index, follow"


# --- meta, hreflang, Open Graph ---------------------------------------------------------------


def test_title_description_hreflang_and_open_graph(web: TestClient, db: Session) -> None:
    _masters(db, 4, specs=("santekhnik",), cities=("dushanbe",))
    ru = web.get("/dushanbe/santekhnik").text
    assert _title(ru) == "Сантехник — Душанбе, мастеров: 4 · UstoiMan"
    description = _attr(ru, r'<meta name="description" content="([^"]*)">')
    assert 0 < len(description) <= 160
    assert "<" not in description
    alternates = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', ru))
    assert alternates == {
        "ru": f"{SITE}/dushanbe/santekhnik",
        "tg": f"{SITE}/tg/dushanbe/santekhnik",
        "x-default": f"{SITE}/dushanbe/santekhnik",
    }
    assert _og(ru, "title") == _title(ru)
    assert _og(ru, "description") == description
    assert _og(ru, "url") == _canonical(ru)
    assert _og(ru, "type") == "website"
    assert _og(ru, "locale") == "ru_RU"

    tg = web.get("/tg/dushanbe/santekhnik").text
    assert _title(tg) == "Сантехник дар Душанбе — 4 усто · UstoiMan"
    assert _canonical(tg) == f"{SITE}/tg/dushanbe/santekhnik"
    assert _og(tg, "locale") == "tg_TJ"
    assert len(_attr(tg, r'<meta name="description" content="([^"]*)">')) <= 160
    tg_alternates = re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', tg)
    assert dict(tg_alternates) == alternates


def test_long_names_keep_the_description_within_160(web: TestClient, db: Session) -> None:
    db.execute(update(Specialty).where(Specialty.slug == "santekhnik").values(name_ru="Ы " * 60))
    db.execute(update(City).where(City.slug == "dushanbe").values(name_ru="Д" * 120))
    _master(db)
    description = _attr(
        web.get("/dushanbe/santekhnik").text, r'<meta name="description" content="([^"]*)">'
    )
    assert len(description) <= 160


def test_markup_in_place_names_stays_escaped(web: TestClient, db: Session) -> None:
    db.execute(update(City).where(City.slug == "dushanbe").values(name_ru='<b>"Д"</b>'))
    _master(db)
    html = web.get("/dushanbe/santekhnik").text
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


# --- language ---------------------------------------------------------------------------------


def test_path_language_overrides_the_cookie_and_the_switch_links_to_the_other_url(
    web: TestClient, db: Session
) -> None:
    _master(db, specs=("elektrik",), cities=("khujand",))
    web.cookies.set("mc_lang", "ru")
    tg = web.get("/tg/khujand/elektrik").text
    assert '<html lang="tg">' in tg
    assert "Барқчӣ" in tg and "Хуҷанд" in tg
    footer = tg.split("<footer")[1]
    assert 'href="/khujand/elektrik"' in footer
    assert "/lang/" not in footer

    web.cookies.set("mc_lang", "tg")
    ru = web.get("/khujand/elektrik").text
    assert '<html lang="ru">' in ru
    assert "Электрик" in ru and "Худжанд" in ru
    footer = ru.split("<footer")[1]
    assert 'href="/tg/khujand/elektrik"' in footer
    assert "/lang/" not in footer
    # other pages follow the cookie (the Russian URL has just set it to ru)
    web.cookies.clear()
    web.cookies.set("mc_lang", "tg")
    assert '<html lang="tg">' in web.get("/search").text


# --- unknown, inactive and hostile slugs ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/nowhere/santekhnik",
        "/dushanbe/no-such",
        "/dushanbe/nowhere/santekhnik",
        "/khujand/sino/santekhnik",  # a district of another city
        "/tg/nowhere/santekhnik",
        "/tg/dushanbe/nowhere/santekhnik",
        "/Dushanbe/santekhnik",
        "/dushanbe/Santekhnik",
        "/dushanbe/a--b",
        "/dushanbe/%00",
        "/dushanbe/%D1%81%D0%B0%D0%BD",
        "/dushanbe/" + "a" * 300,
        "/dushanbe/sino/" + "%27%22%3C",
        "/tg/tg/santekhnik",
    ],
)
def test_unknown_slugs_are_404(web: TestClient, path: str) -> None:
    r = web.get(path)
    assert r.status_code == 404
    assert "robots" not in r.text or len(ROBOTS_RE.findall(r.text)) <= 1


@pytest.mark.parametrize(
    "deactivate",
    [
        lambda db: update(City).where(City.slug == "dushanbe").values(is_active=False),
        lambda db: update(Specialty).where(Specialty.slug == "santekhnik").values(is_active=False),
        lambda db: (
            update(Category)
            .where(
                Category.id
                == select(Specialty.category_id)
                .where(Specialty.slug == "santekhnik")
                .scalar_subquery()
            )
            .values(is_active=False)
        ),
    ],
    ids=["city", "specialty", "category"],
)
def test_inactive_city_or_specialty_is_404(web: TestClient, db: Session, deactivate) -> None:  # type: ignore[no-untyped-def]
    _master(db)
    assert web.get("/dushanbe/santekhnik").status_code == 200
    db.execute(deactivate(db))
    assert web.get("/dushanbe/santekhnik").status_code == 404
    assert web.get("/tg/dushanbe/santekhnik").status_code == 404
    assert web.get("/dushanbe/sino/santekhnik").status_code == 404


def test_inactive_district_is_404(web: TestClient, db: Session) -> None:
    db.execute(update(District).where(District.slug == "sino").values(is_active=False))
    assert web.get("/dushanbe/sino/santekhnik").status_code == 404
    assert web.get("/dushanbe/santekhnik").status_code == 200


def test_tg_404_is_in_tajik(web: TestClient) -> None:
    r = web.get("/tg/nowhere/santekhnik")
    assert r.status_code == 404
    assert '<html lang="tg">' in r.text


def test_existing_routes_still_answer_first(web: TestClient, db: Session) -> None:
    master = _master(db)
    assert web.get("/search").status_code == 200
    assert web.get(f"/m/{master.slug}").status_code == 200
    assert web.get("/lang/tg", params={"next": "/"}).status_code in (302, 303, 307)


# --- neighbour links --------------------------------------------------------------------------


def test_neighbour_links_point_only_to_combinations_with_masters(
    web: TestClient, db: Session
) -> None:
    _masters(db, 2, specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    _masters(db, 2, specs=("santekhnik",), cities=("khujand",))
    _master(db, specs=("santekhnik",), cities=("bokhtar",))
    _master(db, specs=("elektrik",), cities=("dushanbe",))
    _masters(db, 3, specs=("plitochnik",), cities=("bokhtar",))

    html = web.get("/dushanbe/santekhnik").text
    cities = _section(html, "seo-other-cities")
    assert 'href="/khujand/santekhnik"' in cities and 'href="/bokhtar/santekhnik"' in cities
    assert cities.index("/khujand/santekhnik") < cities.index("/bokhtar/santekhnik")  # by count
    assert "/dushanbe/santekhnik" not in cities
    specialties = _section(html, "seo-other-specialties")
    assert 'href="/dushanbe/elektrik"' in specialties
    assert "plitochnik" not in specialties and "santekhnik" not in specialties
    districts = _section(html, "seo-districts")
    assert 'href="/dushanbe/sino/santekhnik"' in districts
    assert "firdavsi" not in districts and "shohmansur" not in districts

    # Tajik pages link to Tajik URLs
    tg = web.get("/tg/dushanbe/santekhnik").text
    assert 'href="/tg/khujand/santekhnik"' in _section(tg, "seo-other-cities")
    assert 'href="/tg/dushanbe/sino/santekhnik"' in _section(tg, "seo-districts")

    # a district page links back to its city page
    district = web.get("/dushanbe/sino/santekhnik").text
    assert 'href="/dushanbe/santekhnik"' in district

    # inactive places and specialties are never linked
    db.execute(update(City).where(City.slug == "khujand").values(is_active=False))
    db.execute(update(Specialty).where(Specialty.slug == "elektrik").values(is_active=False))
    html = web.get("/dushanbe/santekhnik").text
    assert "/khujand/" not in html
    assert "/dushanbe/elektrik" not in html


def test_district_links_need_a_master_who_named_the_district(web: TestClient, db: Session) -> None:
    """Whole-city masters alone would make every district page a near-duplicate of the city."""
    _masters(db, 2, specs=("santekhnik",), cities=("dushanbe",))
    html = web.get("/dushanbe/santekhnik").text
    assert 'id="seo-districts"' not in html
    assert "/dushanbe/sino/" not in html

    _master(db, specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    cache.clear()  # the grouped counts are cached for ten minutes
    districts = _section(web.get("/dushanbe/santekhnik").text, "seo-districts")
    assert districts.count('href="/') == 1
    assert 'href="/dushanbe/sino/santekhnik"' in districts
    # the pill shows how many masters the district page lists (named + whole-city)
    assert re.search(r"Сино <span[^>]*>3</span>", districts)


def test_inactive_district_is_not_linked(web: TestClient, db: Session) -> None:
    _master(db, cities=(), districts=(("dushanbe", "sino"),))
    db.execute(update(District).where(District.slug == "sino").values(is_active=False))
    assert "/dushanbe/sino/" not in web.get("/dushanbe/santekhnik").text


def test_at_most_six_links_per_group(web: TestClient, db: Session) -> None:
    others = ["khujand", "bokhtar", "kulob", "vahdat", "hisor", "tursunzoda", "isfara"]
    for slug in others:
        _master(db, cities=(slug,))
    _master(db)
    cities = _section(web.get("/dushanbe/santekhnik").text, "seo-other-cities")
    assert cities.count('href="/') == 6


# --- safety -----------------------------------------------------------------------------------


def test_seo_pages_show_no_contacts(web: TestClient, db: Session) -> None:
    master = _master(db)
    master.contact_telegram = "secret_tg"
    master.contact_whatsapp = "+992935550000"
    db.flush()
    refresh_search(db, master)
    html = web.get("/dushanbe/santekhnik").text
    assert master.slug in html
    for secret in ("secret_tg", "992935550000", "tel:", "t.me", "wa.me"):
        assert secret not in html


def test_every_page_has_at_most_one_robots_meta(web: TestClient, db: Session) -> None:
    master = _master(db)
    for path in (
        "/",
        "/search",
        "/signin",
        f"/m/{master.slug}",
        "/dushanbe/santekhnik",
        "/dushanbe/sino/santekhnik",
        "/tg/dushanbe/santekhnik",
        "/dushanbe/elektrik",
        "/nowhere/santekhnik",
    ):
        html = web.get(path).text
        assert len(ROBOTS_RE.findall(html)) <= 1, path
        assert html.count('name="robots"') <= 1, path


def test_reserved_segments_never_collide_with_place_slugs(web: TestClient, db: Session) -> None:
    """Review Focus 3: a city or district named like a first path segment of another route
    would hide that route's pages (or the SEO page). The set comes from the routes themselves."""
    paths = []
    for route in web.app.routes:  # type: ignore[attr-defined]
        if hasattr(route, "effective_route_contexts"):  # an included router (FastAPI ≥ 0.140)
            paths += [context.path for context in route.effective_route_contexts()]
        else:
            paths.append(route.path)
    reserved = {"tg"}
    for path in paths:
        first = path.strip("/").split("/")[0]
        if first and not first.startswith("{"):
            reserved.add(first)
    assert {"m", "search", "static", "signin", "account", "media", "lang"} <= reserved

    seeded = load()
    file_slugs = {c["slug"] for c in seeded.cities} | {
        d["slug"] for c in seeded.cities for d in c.get("districts", [])
    }
    db_slugs = set(db.scalars(select(City.slug))) | set(db.scalars(select(District.slug)))
    assert file_slugs, "the seed file lists places"
    assert not (file_slugs | db_slugs) & reserved


# --- language sticks after an SEO page (R-025) -------------------------------------------------


def test_tajik_seo_page_keeps_tajik_afterwards_and_ru_page_resets_it(
    web: TestClient, db: Session
) -> None:
    master = _master(db, specs=("elektrik",), cities=("khujand",))
    web.cookies.clear()
    r = web.get("/tg/khujand/elektrik")
    assert "mc_lang=tg" in r.headers.get("set-cookie", "")
    assert '<html lang="tg">' in web.get(f"/m/{master.slug}").text
    r = web.get("/khujand/elektrik")  # Russian URL: back to ru
    assert "mc_lang=ru" in r.headers.get("set-cookie", "")
    assert '<html lang="ru">' in web.get(f"/m/{master.slug}").text
    # already matching: no new cookie
    assert "mc_lang" not in web.get("/khujand/elektrik").headers.get("set-cookie", "")


def test_seo_404_switch_links_go_home_not_back_to_the_tg_url(web: TestClient) -> None:
    footer = web.get("/tg/nowhere/santekhnik").text.split("<footer")[1]
    assert 'href="/" hreflang="ru"' in footer and 'href="/" hreflang="tg"' in footer
    assert "/tg/nowhere" not in footer and "/lang/" not in footer
