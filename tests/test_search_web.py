"""The /search page and the home page (spec D2, D3, D10, D11.3)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.i18n import LANGS, _catalog
from app.core.security import hash_password
from app.core.time import utcnow
from app.models import Category, User
from app.services import search
from app.services.search_index import refresh_search
from tests.support import PASSWORD, sign_in
from tests.test_search_service import _master, _photos


@pytest.fixture
def web(client: TestClient, db: Session) -> Iterator[TestClient]:
    """The app on the test session: what a test creates is visible to requests, then rolled back."""
    db.execute(text("DELETE FROM master_search"))
    client.app.dependency_overrides[get_db] = lambda: db  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.pop(get_db, None)  # type: ignore[attr-defined]


# --- /search ----------------------------------------------------------------------------------


def test_search_page_lists_cards_linking_to_profiles(web: TestClient, db: Session) -> None:
    master = _master(db, "Иван Петров", headline="Быстро и чисто")
    r = web.get("/search")
    assert r.status_code == 200
    assert f'href="/m/{master.slug}"' in r.text
    assert "Иван Петров" in r.text
    assert '<meta name="robots" content="noindex, follow">' in r.text


def test_cards_show_no_contacts_even_for_a_signed_in_viewer(web: TestClient, db: Session) -> None:
    master = _master(db, "Иван Петров")
    master.contact_telegram = "secret_tg"
    master.contact_whatsapp = "+992935550000"
    db.get(User, master.user_id).phone = "+992901234567"
    db.flush()
    refresh_search(db, master)
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
    assert web.get("/account").status_code == 200  # really signed in
    r = web.get("/search")
    assert master.slug in r.text
    for secret in ("secret_tg", "992935550000", "901234567", "tel:", "t.me", "wa.me"):
        assert secret not in r.text


def test_matches_are_marked_and_markup_in_names_stays_escaped(web: TestClient, db: Session) -> None:
    _master(db, "Иван <b>Петров</b>")
    r = web.get("/search", params={"q": "петров"})
    assert "<mark>Петров</mark>" in r.text
    assert "<b>" not in r.text
    assert "&lt;b&gt;" in r.text


def test_filters_round_trip_in_the_form_pills_and_page_links(web: TestClient, db: Session) -> None:
    for i in range(23):
        _master(db, f"Мастер {i}", specs=("santekhnik",), cities=("dushanbe",))
    params = {"q": "мастер", "s": "santekhnik", "c": "dushanbe", "available": "1", "sort": "newest"}
    r = web.get("/search", params=params)
    html = r.text
    assert '<option value="santekhnik" selected>' in html
    assert '<option value="dushanbe" selected>' in html
    assert 'name="available" value="1" checked' in html
    assert 'value="мастер"' in html
    # pills show the active filters; the next-page link keeps all of them
    assert "search.remove" not in html and "pill" in html
    next_link = (
        next(
            part
            for part in html.split('href="')
            if part.startswith("/search?") and "page=2" in part
        )
        .split('"')[0]
        .replace("&amp;", "&")
    )
    for key in params:
        assert f"{key}=" in next_link
    r2 = web.get(next_link)
    assert r2.status_code == 200
    prev = r2.text.split('rel="prev" href="')[1].split('"')[0].replace("&amp;", "&")
    assert "page=" not in prev and "s=santekhnik" in prev and "available=1" in prev


def test_unknown_values_show_notices_not_errors(web: TestClient, db: Session) -> None:
    _master(db)
    r = web.get("/search", params={"s": "no-such", "c": "nowhere", "sort": "price", "page": "x"})
    assert r.status_code == 200
    for code in ("specialty", "city", "sort", "page"):
        assert f'data-notice="{code}"' in r.text
    r = web.get("/search", params={"d": "sino", "available": "yes", "q": "%%"})
    assert r.status_code == 200
    for code in ("district_without_city", "available", "q"):
        assert f'data-notice="{code}"' in r.text


def test_timeout_is_a_friendly_message_with_status_200(
    web: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.search import SearchResult

    def slow(db: Session, query, *, lang: str) -> SearchResult:  # type: ignore[no-untyped-def]
        return SearchResult(items=[], total=0, page=1, pages=0, timed_out=True)

    monkeypatch.setattr(search, "search", slow)
    r = web.get("/search", params={"q": "сантехник"})
    assert r.status_code == 200
    assert 'id="search-timeout"' in r.text


def test_empty_result_offers_did_you_mean_dropping_the_text(web: TestClient, db: Session) -> None:
    _master(db, specs=("santekhnik",))
    r = web.get("/search", params={"q": "электрик", "available": "1"})
    html = r.text.replace("&amp;", "&")
    assert 'id="search-suggestion"' in html
    assert "/search?s=elektrik&available=1" in html


def test_district_suggestion_sets_city_and_district(web: TestClient, db: Session) -> None:
    _master(db)
    r = web.get("/search", params={"q": "sino", "s": "santekhnik"})
    html = r.text.replace("&amp;", "&")
    assert "c=dushanbe&d=sino" in html or "d=sino&c=dushanbe" in html
    assert "s=santekhnik" in html  # other params kept


def test_specialty_hint_chip_adds_s_and_keeps_q(web: TestClient, db: Session) -> None:
    _master(db, "Сантехник Али", specs=("santekhnik",))
    r = web.get("/search", params={"q": "сантехник"})
    html = r.text.replace("&amp;", "&")
    assert 'id="search-hint"' in html
    assert "/search?q=%D1%81%D0%B0%D0%BD%D1%82%D0%B5%D1%85%D0%BD%D0%B8%D0%BA&s=santekhnik" in html


def test_card_shows_availability_and_photo_strip(web: TestClient, db: Session) -> None:
    master = _master(db, available=False)
    _photos(db, master, 3)
    r = web.get("/search")
    assert r.text.count('class="aspect-square') == 3
    assert "Сейчас не принимает заказы" in r.text


# --- home -------------------------------------------------------------------------------------


def test_home_has_search_form_and_category_tiles(web: TestClient, db: Session) -> None:
    r = web.get("/")
    html = r.text
    assert 'action="/search"' in html and 'method="get"' in html
    assert 'name="q"' in html and 'name="s"' in html and 'name="c"' in html
    assert "<optgroup" in html
    slugs = list(db.scalars(select(Category.slug).where(Category.is_active)))
    assert len(slugs) == 12
    for slug in slugs:
        assert f'href="/search?cat={slug}"' in html
    assert "Скоро здесь будет сервис" not in html
    assert "Окружение" not in html


def test_home_in_tajik_uses_locale_texts(web: TestClient) -> None:
    web.cookies.set("mc_lang", "tg")
    r = web.get("/")
    assert "Мастера Таджикистана" not in r.text


# --- texts ------------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_every_notice_code_has_a_text(lang: str) -> None:
    catalog = _catalog(lang)
    for code in search.NOTICE_CODES:
        assert catalog.get(f"search.notice_{code}"), (lang, code)
