"""sitemap.xml (spec D6, D11.5; Review Focus 1)."""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.time import utcnow
from app.models import PortfolioCollection, PortfolioPhoto, Specialty, User
from app.services import cache, search, sitemap
from app.services.storage import new_key
from tests.test_search_service import _master

SITE = "https://ustoi.example"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}


@pytest.fixture
def web(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SITE_URL", SITE)
    get_settings.cache_clear()
    db.execute(text("DELETE FROM master_search"))
    client.app.dependency_overrides[get_db] = lambda: db  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.pop(get_db, None)  # type: ignore[attr-defined]


def _urls(web: TestClient) -> dict[str, ET.Element]:
    r = web.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.content)  # noqa: S314 (our own output)  # parses: well-formed XML
    assert root.tag == f"{{{NS['s']}}}urlset"
    out = {}
    for url in root.findall("s:url", NS):
        loc = url.findtext("s:loc", namespaces=NS)
        assert loc is not None and loc not in out
        out[loc] = url
    return out


def _collection(db: Session, master, *, hidden=False, photo_deleted=False, photo_hidden=False):  # type: ignore[no-untyped-def]
    collection = PortfolioCollection(
        master_id=master.id, title="Ванная", hidden_at=utcnow() if hidden else None
    )
    db.add(collection)
    db.flush()
    db.add(
        PortfolioPhoto(
            collection_id=collection.id,
            storage_key=new_key("p"),
            thumb_key=new_key("p"),
            width=10,
            height=10,
            deleted_at=utcnow() if photo_deleted else None,
            hidden_at=utcnow() if photo_hidden else None,
        )
    )
    db.flush()
    return collection


def _three(db: Session, **kwargs):  # type: ignore[no-untyped-def]
    return [_master(db, f"Мастер {i}", **kwargs) for i in range(3)]


def test_valid_xml_with_content_type(web: TestClient, db: Session) -> None:
    r = web.get("/sitemap.xml")
    assert r.headers["content-type"] == "application/xml; charset=utf-8"
    root = ET.fromstring(r.content)  # noqa: S314 (our own output)
    assert root.tag.endswith("urlset")
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in r.text


def test_indexable_seo_pages_have_alternates(web: TestClient, db: Session) -> None:
    _three(db, specs=("santekhnik",), cities=("dushanbe",))
    urls = _urls(web)
    ru, tg = f"{SITE}/dushanbe/santekhnik", f"{SITE}/tg/dushanbe/santekhnik"
    assert ru in urls and tg in urls
    for page in (urls[ru], urls[tg]):
        links = {
            link.get("hreflang"): link.get("href")
            for link in page.findall("x:link", NS)
            if link.get("rel") == "alternate"
        }
        assert links == {"ru": ru, "tg": tg, "x-default": ru}


def test_pages_with_fewer_than_three_masters_are_left_out(web: TestClient, db: Session) -> None:
    for i in range(2):
        _master(db, f"Мастер {i}", specs=("santekhnik",), cities=("dushanbe",))
    urls = _urls(web)
    assert not any("santekhnik" in loc for loc in urls)


def test_district_page_needs_three_who_named_it(web: TestClient, db: Session) -> None:
    _three(db, specs=("santekhnik",), cities=("dushanbe",))  # whole city only
    for i in range(2):
        _master(db, f"Н{i}", specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    assert f"{SITE}/dushanbe/sino/santekhnik" not in _urls(web)
    cache.clear()
    _master(db, "Н3", specs=("santekhnik",), cities=(), districts=(("dushanbe", "sino"),))
    urls = _urls(web)
    assert f"{SITE}/dushanbe/sino/santekhnik" in urls
    assert f"{SITE}/tg/dushanbe/sino/santekhnik" in urls


def test_profiles_and_public_collections(web: TestClient, db: Session) -> None:
    master = _master(db, "Иван")
    shown = _collection(db, master)
    hidden = _collection(db, master, hidden=True)
    all_deleted = _collection(db, master, photo_deleted=True)
    photo_hidden = _collection(db, master, photo_hidden=True)
    urls = _urls(web)
    assert f"{SITE}/m/{master.slug}" in urls
    assert urls[f"{SITE}/m/{master.slug}"].findtext("s:lastmod", namespaces=NS)
    assert f"{SITE}/m/{master.slug}/c/{shown.id}" in urls
    assert urls[f"{SITE}/m/{master.slug}/c/{shown.id}"].findtext("s:lastmod", namespaces=NS)
    for gone in (hidden, all_deleted, photo_hidden):
        assert f"{SITE}/m/{master.slug}/c/{gone.id}" not in urls


def test_draft_is_not_listed(web: TestClient, db: Session) -> None:
    draft = _master(db, "Черновик", ready=False)
    assert f"{SITE}/m/{draft.slug}" not in _urls(web)


def test_master_blocked_by_sql_is_not_listed_even_without_a_refresh(
    web: TestClient, db: Session
) -> None:
    masters = _three(db, specs=("santekhnik",), cities=("dushanbe",))
    blocked = masters[0]
    _collection(db, blocked)
    assert f"{SITE}/dushanbe/santekhnik" in _urls(web)
    cache.clear()
    db.execute(update(User).where(User.id == blocked.user_id).values(blocked_at=utcnow()))
    urls = _urls(web)
    assert f"{SITE}/m/{blocked.slug}" not in urls
    assert not any(loc.startswith(f"{SITE}/m/{blocked.slug}/") for loc in urls)
    assert f"{SITE}/m/{masters[1].slug}" in urls
    assert f"{SITE}/dushanbe/santekhnik" not in urls  # only 2 visible left
    assert f"{SITE}/tg/dushanbe/santekhnik" not in urls


def test_inactive_specialty_is_left_out(web: TestClient, db: Session) -> None:
    _three(db, specs=("santekhnik",), cities=("dushanbe",))
    db.execute(update(Specialty).where(Specialty.slug == "santekhnik").values(is_active=False))
    assert not any("santekhnik" in loc for loc in _urls(web))


def test_inactive_city_is_left_out(web: TestClient, db: Session) -> None:
    _three(db, specs=("santekhnik",), cities=("dushanbe",))
    db.execute(text("UPDATE cities SET is_active = false WHERE slug = 'dushanbe'"))
    assert not any("/dushanbe/" in loc for loc in _urls(web))


def test_body_is_cached_for_ten_minutes(
    web: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache, "now", lambda: clock[0])
    first = _master(db, "Первый")
    body = web.get("/sitemap.xml").content
    second = _master(db, "Второй")
    clock[0] += 599
    assert web.get("/sitemap.xml").content == body  # hit: the new master is not there yet
    assert f"/m/{second.slug}".encode() not in body
    clock[0] += 2
    fresh = web.get("/sitemap.xml").content
    assert f"/m/{second.slug}".encode() in fresh
    assert f"/m/{first.slug}".encode() in fresh


def test_url_guard_stops_at_the_limit(
    web: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for i in range(5):
        _master(db, f"М{i}")
    monkeypatch.setattr(sitemap, "MAX_URLS", 3)
    with caplog.at_level(logging.WARNING):
        urls = _urls(web)
    assert len(urls) == 3
    assert "50 000" in caplog.text or "limit" in caplog.text


def test_robots_points_to_the_sitemap_route(web: TestClient) -> None:
    assert web.get("/sitemap.xml").status_code == 200  # not swallowed by the SEO catch-all


def test_seo_entries_use_fresh_counts_not_the_ten_minute_count_cache(
    web: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache, "now", lambda: clock[0])
    _master(db, "Первый")
    _master(db, "Второй")
    search.cached_counts_by_city_specialty(db)  # a page view caches the counts at t=0
    _master(db, "Третий")
    clock[0] += 599  # the counts cache is still warm; the sitemap body was never built
    assert f"{SITE}/dushanbe/santekhnik" in _urls(web)


def test_entry_xml_escapes_special_characters() -> None:
    loc = 'https://x.example/a?b=1&c=<2>"q"'
    xml = sitemap._entry_xml(loc, None, {'t"g': 'https://x.example/?a=1&b="2"<'})
    doc = f'<urlset xmlns="{NS["s"]}" xmlns:xhtml="{NS["x"]}">{xml}</urlset>'
    root = ET.fromstring(doc)  # noqa: S314 (our own output)
    url = root.find("s:url", NS)
    assert url is not None and url.findtext("s:loc", namespaces=NS) == loc
    link = url.find("x:link", NS)
    assert link is not None
    assert link.get("hreflang") == 't"g' and link.get("href") == 'https://x.example/?a=1&b="2"<'
    assert "&amp;" in xml and "&lt;" in xml and "&quot;" in xml
