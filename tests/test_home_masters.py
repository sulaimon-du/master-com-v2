"""The home page: equal category tiles and the masters block with quick filters (R-028)."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import hash_password
from app.core.time import utcnow
from app.models import MasterProfile, User
from app.services import search
from app.services.search_index import refresh_search
from tests.support import PASSWORD, sign_in
from tests.test_search_service import _city, _master, _photos


@pytest.fixture
def web(client: TestClient, db: Session) -> Iterator[TestClient]:
    """The app on the test session: what a test creates is visible to requests, then rolled back."""
    db.execute(text("DELETE FROM master_search"))
    client.app.dependency_overrides[get_db] = lambda: db  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.pop(get_db, None)  # type: ignore[attr-defined]


def _block(html: str) -> str:
    """The masters block (chips, tiles, "all masters" link) of a home page or the fragment."""
    start = html.index('id="home-masters-block"')
    end = html.index("<!-- /home-masters-block -->", start)
    return html[start:end]


def _profile_links(html: str) -> list[str]:
    return re.findall(r'href="/m/([a-z0-9-]+)"', html)


def _chips(html: str) -> list[str]:
    return re.findall(r'<a [^>]*data-chip="([a-z0-9-]+)"', html)


# --- category tiles ---------------------------------------------------------------------------


def test_category_tiles_are_equal_height_and_clamped(web: TestClient) -> None:
    html = web.get("/").text
    grid = re.search(r'<ul class="([^"]*)"[^>]*id="home-cat-tiles"', html)
    assert grid is not None and "auto-rows-fr" in grid.group(1).split()
    tiles = re.findall(r'<li class="h-full"><a class="([^"]*)" href="/search\?cat=', html)
    assert len(tiles) == 12
    for classes in tiles:
        assert {"card", "h-full", "items-center"} <= set(classes.split())
    assert html.count('class="line-clamp-3') == 12
    # The full name stays in the text and in the title (catalog data, autoescaped).
    assert 'title="Отделка и ремонт: штукатурка, плитка, полы, потолки"' in html


# --- masters block ----------------------------------------------------------------------------


def test_block_shows_at_most_eight_masters(web: TestClient, db: Session) -> None:
    for i in range(9):
        _master(db, f"Мастер {i}")
    html = web.get("/").text
    assert 'id="masters"' in html
    assert len(_profile_links(_block(html))) == 8


def test_only_visible_masters_are_shown(web: TestClient, db: Session) -> None:
    kept = _master(db, "Видимый")
    draft = _master(db, "Черновик", ready=False)
    hidden = _master(db, "Скрытый")
    hidden.hidden_at = utcnow()
    db.flush()
    refresh_search(db, hidden)
    blocked = _master(db, "Заблокированный")
    db.execute(text("UPDATE users SET blocked_at = now() WHERE id = :id"), {"id": blocked.user_id})
    db.expire_all()
    html = _block(web.get("/").text)
    assert _profile_links(html) == [kept.slug]
    for gone in (draft, hidden, blocked):
        assert gone.slug not in html


def test_city_chips_are_the_top_four_cities(web: TestClient, db: Session) -> None:
    # Counts: dushanbe 4, khujand 3, bokhtar 2, kulob 2 (one master with two specialties still
    # counts once), vahdat 1.
    for city, n in (("dushanbe", 4), ("khujand", 3), ("bokhtar", 1), ("vahdat", 1)):
        for _ in range(n):
            _master(db, cities=(city,))
    _master(db, specs=("santekhnik", "elektrik"), cities=("bokhtar",))
    _master(db, specs=("santekhnik", "elektrik"), cities=("kulob",))
    _master(db, cities=("kulob",))
    _master(db, ready=False, cities=("vahdat",))  # drafts do not count
    html = web.get("/").text
    assert _chips(_block(html)) == ["all", "dushanbe", "khujand", "bokhtar", "kulob", "available"]
    assert 'href="/?m_city=dushanbe#masters"' in html
    assert 'hx-get="/partials/home-masters?city=dushanbe"' in html


def test_counts_by_city_counts_each_master_once(db: Session) -> None:
    db.execute(text("DELETE FROM master_search"))
    _master(db, specs=("santekhnik", "elektrik"), cities=("dushanbe", "khujand"))
    _master(db, cities=("dushanbe",))
    _master(db, ready=False, cities=("dushanbe",))
    counts = search.counts_by_city(db)
    assert counts == {_city(db, "dushanbe").id: 2, _city(db, "khujand").id: 1}


def test_city_chip_filters_and_is_marked_current(web: TestClient, db: Session) -> None:
    in_dushanbe = _master(db, cities=("dushanbe",))
    in_khujand = _master(db, cities=("khujand",))
    html = _block(web.get("/", params={"m_city": "khujand"}).text)
    assert _profile_links(html) == [in_khujand.slug]
    assert in_dushanbe.slug not in html
    assert re.search(r'<a [^>]*aria-current="true"[^>]*data-chip="khujand"', html)
    assert not re.search(r'<a [^>]*aria-current="true"[^>]*data-chip="all"', html)
    assert 'href="/search?c=khujand&amp;sort=rating"' in html


def test_available_toggle_filters_and_keeps_the_city(web: TestClient, db: Session) -> None:
    free = _master(db, cities=("dushanbe",))
    busy = _master(db, cities=("dushanbe",), available=False)
    _master(db, cities=("khujand",))
    html = _block(web.get("/", params={"m_city": "dushanbe", "m_available": "1"}).text)
    assert _profile_links(html) == [free.slug]
    assert busy.slug not in html
    assert re.search(r'<a [^>]*aria-current="true"[^>]*data-chip="available"', html)
    # The toggle switches availability off and keeps the city; city chips keep availability.
    assert 'href="/?m_city=dushanbe#masters"' in html
    assert 'href="/?m_city=khujand&amp;m_available=1#masters"' in html
    assert 'href="/?m_available=1#masters"' in html  # "all"
    assert 'href="/search?c=dushanbe&amp;available=1&amp;sort=rating"' in html


@pytest.mark.parametrize(
    "params",
    [
        {"m_city": "<script>"},
        {"m_city": "no-such-city"},
        {"m_city": "khorog"},  # a real city, but not one of the chips
        {"m_available": "yes"},
        {"m_city": "x" * 500, "m_available": "\x00"},
    ],
)
def test_invalid_params_fall_back_to_all(
    web: TestClient, db: Session, params: dict[str, str]
) -> None:
    # Chips: dushanbe 2, khujand 2, bokhtar 1, kulob 1; khorog (1, later in the catalog) is out.
    master = _master(db, cities=("dushanbe",))
    for city in ("dushanbe", "khujand", "khujand", "bokhtar", "kulob", "khorog"):
        _master(db, cities=(city,))
    assert "khorog" not in _chips(_block(web.get("/").text))
    r = web.get("/", params=params)
    assert r.status_code == 200
    html = _block(r.text)
    assert master.slug in html
    assert re.search(r'<a [^>]*aria-current="true"[^>]*data-chip="all"', html)
    assert "&lt;script&gt;" not in html and "<script>" not in html
    fragment = web.get("/partials/home-masters", params={k[2:]: v for k, v in params.items()})
    assert fragment.status_code == 200
    assert master.slug in fragment.text


def test_fragment_returns_only_the_block(web: TestClient, db: Session) -> None:
    master = _master(db, cities=("khujand",))
    r = web.get("/partials/home-masters", params={"city": "khujand", "available": "1"})
    assert r.status_code == 200
    assert "<html" not in r.text and "<header" not in r.text
    assert r.text.lstrip().startswith('<div id="home-masters-block"')
    assert _profile_links(r.text) == [master.slug]
    assert r.headers["Cache-Control"] == "no-store"


def test_chips_are_links_enhanced_with_htmx(web: TestClient, db: Session) -> None:
    _master(db, cities=("dushanbe",))
    html = _block(web.get("/").text)
    chip = re.search(r'<a [^>]*data-chip="dushanbe"[^>]*>', html)
    assert chip is not None
    tag = chip.group(0)
    assert 'href="/?m_city=dushanbe#masters"' in tag
    assert 'hx-get="/partials/home-masters?city=dushanbe"' in tag
    assert 'hx-target="#home-masters-block"' in tag
    assert 'hx-swap="outerHTML"' in tag
    assert 'hx-push-url="/?m_city=dushanbe#masters"' in tag
    assert "style=" not in html and "<script" not in html


def _signed_in_viewer(web: TestClient, db: Session) -> None:
    db.add(
        User(
            email="viewer@example.com",
            name="Зритель",
            password_hash=hash_password(PASSWORD),
            email_verified_at=utcnow(),
            locale="ru",
        )
    )
    db.flush()
    assert sign_in(web, "viewer@example.com").status_code == 303


@pytest.mark.parametrize("signed_in", [False, True])
def test_no_contacts_for_guests_or_signed_in(web: TestClient, db: Session, signed_in: bool) -> None:
    master = _master(db, "Иван Петров")
    _photos(db, master, 4)
    master.contact_telegram = "secret_tg"
    master.contact_whatsapp = "+992935550000"
    db.get(User, master.user_id).phone = "+992901234567"
    db.flush()
    refresh_search(db, master)
    if signed_in:
        _signed_in_viewer(web, db)
    r = web.get("/")
    assert r.status_code == 200
    html = _block(r.text)
    assert master.slug in html
    assert html.count('class="aspect-square') == 3  # compact card: at most 3 photos
    for secret in ("secret_tg", "992935550000", "901234567", "tel:", "t.me", "wa.me"):
        assert secret not in r.text


def test_empty_state(web: TestClient) -> None:
    html = _block(web.get("/", params={"m_available": "1"}).text)
    assert _profile_links(html) == []
    assert "Пока никого нет" in html
    assert _chips(html) == ["all", "available"]


def test_block_in_tajik(web: TestClient, db: Session) -> None:
    _master(db)
    web.cookies.set("mc_lang", "tg")
    html = web.get("/").text
    assert "Все мастера" not in html and "Свободны сейчас" not in html
    assert "Ҳамаи устоҳо" in html


def test_markup_in_master_names_stays_escaped(web: TestClient, db: Session) -> None:
    master: MasterProfile = _master(db, "Иван <b>Петров</b>")
    html = _block(web.get("/").text)
    assert master.slug in html
    assert "<b>" not in html and "&lt;b&gt;" in html
