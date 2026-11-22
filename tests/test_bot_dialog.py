import pytest
from sqlalchemy import select

from app.bot import dialog
from app.bot.dialog import TgUser
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.i18n import translate
from app.core.time import utcnow
from app.models.user import User
from app.services import auth, rate_limit, telegram
from tests.support import make_user

pytestmark = pytest.mark.usefixtures("clean_auth")

SITE = "http://testserver"
TG_ID = 9001


@pytest.fixture(autouse=True)
def tg(monkeypatch, settings):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "MasterComTestBot")
    monkeypatch.setenv("SITE_URL", "http://testserver/")  # trailing slash on purpose
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _tg(telegram_id: int = TG_ID, lang: str | None = "ru") -> TgUser:
    return TgUser(id=telegram_id, first_name="Дилшод", language_code=lang)


def _link_token(user_id: int) -> str:
    with get_sessionmaker()() as db:
        user = db.get(User, user_id)
        raw = telegram.issue_link(db, user)
        db.commit()
    return raw


def _linked_user(telegram_id: int = TG_ID) -> int:
    uid = make_user()
    with get_sessionmaker()() as db:
        db.get(User, uid).telegram_id = telegram_id
        db.commit()
    return uid


def _t(key: str, lang: str = "ru", **kw: str) -> str:
    return translate(lang, key, **kw)


def _db_user(uid: int) -> User:
    with get_sessionmaker()() as db:
        return db.scalar(select(User).where(User.id == uid))


def test_start_login_gives_one_url_button() -> None:
    r = dialog.on_start(_tg(), "login")
    assert r.text == _t("bot.signin_link")
    (b,) = r.buttons
    assert b.url is not None and b.callback is None
    prefix = f"{SITE}/auth/telegram/"
    assert b.url.startswith(prefix)
    assert len(b.url.removeprefix(prefix)) == 43
    assert "//auth" not in b.url


def test_start_login_rate_limited_is_neutral() -> None:
    for _ in range(5):
        assert dialog.on_start(_tg(), "login").buttons
    r = dialog.on_start(_tg(), "login")
    assert r.text == _t("bot.signin_unavailable")
    assert r.buttons == ()


def test_start_link_shows_masked_account_and_two_buttons() -> None:
    uid = make_user(email="ana@example.com", name="Ана")
    raw = _link_token(uid)
    r = dialog.on_start(_tg(), f"link_{raw}")
    assert "А**" in r.text and "a**@example.com" in r.text
    assert r.text == _t("bot.link_confirm", account="А** · a**@example.com")
    yes, no = r.buttons
    assert yes.callback is not None and yes.callback.startswith("l:")
    assert no.callback == "x"
    assert raw not in r.text and all(raw not in (b.callback or "") for b in r.buttons)


def test_start_link_invalid_token() -> None:
    assert dialog.on_start(_tg(), "link_nope").text == _t("bot.link_expired")


def test_start_unknown_payload_is_welcome() -> None:
    assert dialog.on_start(_tg(), "").text == _t("bot.welcome")
    assert dialog.on_start(_tg(), "whatever").text == _t("bot.welcome")


def test_callback_yes_links_and_asks_for_contact() -> None:
    uid = make_user()
    raw = _link_token(uid)
    yes = dialog.on_start(_tg(), f"link_{raw}").buttons[0]
    r = dialog.on_callback(_tg(), yes.callback or "")
    assert r.text == _t("bot.linked")
    assert r.ask_contact is True
    assert _db_user(uid).telegram_id == TG_ID
    # The token works once.
    again = dialog.on_callback(_tg(), yes.callback or "")
    assert again.text == _t("bot.link_expired")


def test_callback_yes_with_verified_phone_does_not_ask_for_contact() -> None:
    uid = make_user()
    with get_sessionmaker()() as db:
        user = db.get(User, uid)
        user.phone, user.phone_verified_at = "+992900112233", utcnow()
        db.commit()
    raw = _link_token(uid)
    yes = dialog.on_start(_tg(), f"link_{raw}").buttons[0]
    r = dialog.on_callback(_tg(), yes.callback or "")
    assert r.text == _t("bot.linked_done")
    assert r.ask_contact is False
    assert _db_user(uid).telegram_id == TG_ID


def test_callback_same_telegram_already_linked_is_linked() -> None:
    uid = _linked_user()
    raw = _link_token(uid)
    yes = dialog.on_start(_tg(), f"link_{raw}").buttons[0]
    assert dialog.on_callback(_tg(), yes.callback or "").text == _t("bot.linked")


def test_callback_forged_for_another_telegram_user() -> None:
    uid = make_user()
    raw = _link_token(uid)
    yes = dialog.on_start(_tg(), f"link_{raw}").buttons[0]
    r = dialog.on_callback(_tg(777), yes.callback or "")
    assert r.text == _t("bot.link_expired")
    assert r.ask_contact is False
    assert _db_user(uid).telegram_id is None
    # The real recipient can still use it: the forged tap did not burn the token.
    assert dialog.on_callback(_tg(), yes.callback or "").text == _t("bot.linked")


def test_callback_garbage_and_cancel() -> None:
    uid = make_user()
    assert dialog.on_callback(_tg(), "l:1:zzzz").text == _t("bot.link_expired")
    assert dialog.on_callback(_tg(), "???").text == _t("bot.link_expired")
    raw = _link_token(uid)
    dialog.on_start(_tg(), f"link_{raw}")
    r = dialog.on_callback(_tg(), "x")
    assert r.text == _t("bot.cancelled")
    assert _db_user(uid).telegram_id is None
    with get_sessionmaker()() as db:
        assert telegram.link_preview(db, raw) is not None  # token untouched


def test_callback_telegram_taken_and_other_linked() -> None:
    _linked_user(TG_ID)
    uid = make_user(email="b@example.com", name="Бо")
    yes = dialog.on_start(_tg(), f"link_{_link_token(uid)}").buttons[0]
    assert dialog.on_callback(_tg(), yes.callback or "").text == _t("bot.telegram_taken")
    with get_sessionmaker()() as db:
        db.get(User, uid).telegram_id = 6000
        db.commit()
    yes = dialog.on_start(_tg(), f"link_{_link_token(uid)}").buttons[0]
    assert dialog.on_callback(_tg(), yes.callback or "").text == _t("bot.telegram_other_linked")


def test_callback_rate_limited_persists() -> None:
    uid = make_user()
    yes = dialog.on_start(_tg(), f"link_{_link_token(uid)}").buttons[0]
    with get_sessionmaker()() as db:
        for _ in range(20):
            rate_limit.allow(db, "tg_action", str(TG_ID), limit=20, window=auth.HOUR)
        db.commit()
    assert dialog.on_callback(_tg(), yes.callback or "").text == _t("bot.rate_limited")


def test_start_phone_unlinked_points_to_account() -> None:
    r = dialog.on_start(_tg(), "phone")
    assert r.text == _t("bot.not_linked")
    assert r.ask_contact is False
    (b,) = r.buttons
    assert b.url == f"{SITE}/account"


def test_start_phone_linked_asks_for_contact() -> None:
    _linked_user()
    r = dialog.on_start(_tg(), "phone")
    assert r.ask_contact is True
    assert r.text == _t("bot.ask_phone")


def test_contact_own_phone_verifies() -> None:
    uid = _linked_user()
    r = dialog.on_contact(_tg(), TG_ID, "+992 900 11 22 33")
    assert r.text == _t("bot.phone_ok")
    assert r.remove_keyboard is True
    assert r.buttons[0].url == f"{SITE}/account"
    user = _db_user(uid)
    assert user.phone == "+992900112233" and user.phone_verified_at is not None
    # Same number again, then a different one.
    assert dialog.on_contact(_tg(), TG_ID, "+992900112233").text == _t("bot.phone_already")
    assert dialog.on_contact(_tg(), TG_ID, "+992900000000").text == _t("bot.phone_change_support")


def test_contact_not_own_or_invalid() -> None:
    _linked_user()
    assert dialog.on_contact(_tg(), 1234, "+992900112233").text == _t("bot.phone_not_own")
    assert dialog.on_contact(_tg(), None, "+992900112233").text == _t("bot.phone_not_own")
    assert dialog.on_contact(_tg(), TG_ID, "abc").text == _t("bot.phone_not_own")


@pytest.mark.parametrize(("contact_id", "phone"), [(1234, "+992900112233"), (TG_ID, "abc")])
def test_contact_refused_shows_the_contact_button_again(contact_id: int, phone: str) -> None:
    uid = _linked_user()
    r = dialog.on_contact(_tg(lang="tg"), contact_id, phone)
    assert r.ask_contact is True
    assert r.remove_keyboard is False
    assert r.lang == _db_user(uid).locale  # the account's language wins over Telegram's


def test_contact_phone_taken() -> None:
    other = make_user(email="o@example.com", name="О")
    with get_sessionmaker()() as db:
        o = db.get(User, other)
        o.phone, o.phone_verified_at = "+992900112233", utcnow()
        db.commit()
    _linked_user()
    assert dialog.on_contact(_tg(), TG_ID, "+992900112233").text == _t("bot.phone_taken")


def test_contact_not_linked_and_rate_limited() -> None:
    assert dialog.on_contact(_tg(), TG_ID, "+992900112233").text == _t("bot.not_linked")
    _linked_user(TG_ID)
    for _ in range(20):
        dialog.on_contact(_tg(), 1, "+992900112233")  # not own, but counted
    assert dialog.on_contact(_tg(), TG_ID, "+992900112233").text == _t("bot.rate_limited")


def test_language_from_telegram_without_account() -> None:
    r = dialog.on_start(_tg(lang="tg"), "")
    assert r.text == _t("bot.welcome", "tg")
    assert r.text != _t("bot.welcome", "ru")


def test_language_of_the_account_wins() -> None:
    _linked_user()  # locale ru
    assert dialog.on_start(_tg(lang="tg"), "phone").text == _t("bot.ask_phone", "ru")


def test_text_gives_help() -> None:
    assert dialog.on_text(_tg()).text == _t("bot.help")
