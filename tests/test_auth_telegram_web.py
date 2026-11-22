import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.services import telegram
from tests.support import csrf_token, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

BOT = "https://t.me/MasterComTestBot"


@pytest.fixture(autouse=True)
def _telegram_off(monkeypatch):  # type: ignore[no-untyped-def]
    """Off by default, even when a developer's .env has a bot (process env wins over .env)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")
    get_settings.cache_clear()


@pytest.fixture
def tg(monkeypatch, settings):  # type: ignore[no-untyped-def]
    """Telegram sign-in on."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "MasterComTestBot")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _issue(telegram_id: int = 555, first_name: str = "Дилшод") -> str:
    with get_sessionmaker()() as db:
        raw = telegram.issue_signin(
            db, telegram_id=telegram_id, first_name=first_name, language_code="ru"
        )
        db.commit()
    assert raw is not None
    return raw


def test_telegram_button_hidden_when_not_configured(client: TestClient) -> None:
    assert "t.me" not in client.get("/signin").text
    assert "t.me" not in client.get("/register").text
    assert client.get("/auth/telegram/abc").status_code == 404


def test_telegram_button_links_to_the_bot(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    assert f"{BOT}?start=login" in client.get("/signin").text
    assert f"{BOT}?start=login" in client.get("/register").text


def test_signin_page_get_does_not_use_the_token(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    raw = _issue()
    for _ in range(2):
        r = client.get(f"/auth/telegram/{raw}")
        assert r.status_code == 200
        assert "Дилшод" in r.text
        assert r.headers["cache-control"] == "no-store"
        assert "mc_session" not in r.headers.get("set-cookie", "")
    r = post(client, f"/auth/telegram/{raw}")
    assert r.status_code == 303
    assert r.headers["location"] == "/account"


def test_signin_post_needs_csrf(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    raw = _issue()
    client.get(f"/auth/telegram/{raw}")
    assert client.post(f"/auth/telegram/{raw}", data={}).status_code == 403
    assert post(client, f"/auth/telegram/{raw}").status_code == 303


def test_signin_post_rotates_the_session(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    make_user()
    sign_in(client)
    old = client.cookies.get("mc_session")
    assert old
    r = post(client, f"/auth/telegram/{_issue()}")
    assert r.status_code == 303
    assert r.headers["location"] == "/account"
    new = client.cookies.get("mc_session")
    assert new and new != old
    assert client.get("/account").status_code == 200


def test_bad_or_used_signin_link_shows_a_friendly_page(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/auth/telegram/not-a-token")
    assert r.status_code == 400
    assert "Ссылка устарела" in r.text
    assert post(client, "/auth/telegram/not-a-token").status_code == 400
    raw = _issue()
    assert post(client, f"/auth/telegram/{raw}").status_code == 303
    r = post(client, f"/auth/telegram/{raw}")
    assert r.status_code == 400
    assert client.get(f"/auth/telegram/{raw}").status_code == 400


def test_link_start_redirects_to_the_bot_with_a_token(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    make_user()
    sign_in(client)
    r = post(client, "/account/telegram/link")
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith(f"{BOT}?start=link_")
    assert len(location.removeprefix(f"{BOT}?start=link_")) == 43


def test_link_start_needs_a_signed_in_user(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    r = post(client, "/account/telegram/link")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/signin")
    assert client.post("/account/telegram/link", data={}).status_code == 403


def test_link_start_is_rate_limited(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    make_user()
    sign_in(client)
    for _ in range(10):
        assert post(client, "/account/telegram/link").status_code == 303
    assert post(client, "/account/telegram/link").status_code == 429


def test_link_start_is_404_when_not_configured(client: TestClient) -> None:
    make_user()
    sign_in(client)
    assert post(client, "/account/telegram/link").status_code == 404


def test_account_shows_telegram_and_phone_state(client: TestClient, tg) -> None:  # type: ignore[no-untyped-def]
    uid = make_user()
    sign_in(client)
    page = client.get("/account").text
    assert "/account/telegram/link" in page
    assert "+992900000001" not in page
    with get_sessionmaker()() as db:
        db.execute(
            text("UPDATE users SET telegram_id = 77, phone = '+992900000001' WHERE id = :i"),
            {"i": uid},
        )
        db.commit()
    page = client.get("/account").text
    assert "/account/telegram/link" not in page
    assert "+992900000001" in page
    assert f"{BOT}?start=phone" in page
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE users SET phone_verified_at = now() WHERE id = :i"), {"i": uid})
        db.commit()
    page = client.get("/account").text
    assert "+992900000001" in page
    assert f"{BOT}?start=phone" not in page
    assert csrf_token(client)
