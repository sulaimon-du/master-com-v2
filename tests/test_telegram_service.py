from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, update

from app.core.time import utcnow
from app.models.auth import TelegramSignin
from app.models.user import User
from app.services import telegram
from app.services.auth import Outcome
from app.services.telegram import (
    bot_lang,
    issue_signin,
    mask_account,
    normalize_phone,
    sign_in_with_telegram,
    signin_preview,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("992901234567", "+992901234567"),
        ("+992 90 123-45-67", "+992901234567"),
        ("123", None),
        ("0901234567", None),
        ("+0901234567", None),
        ("", None),
        ("   ", None),
        ("+992 90 abc 4567", None),
        ("tel:992901234567", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_mask_account_name_and_email():
    assert mask_account(User(name="Ана", email="ana@gmail.com")) == "А** · a**@gmail.com"


def test_mask_account_name_only():
    assert mask_account(User(name="Ана")) == "А**"


def test_mask_account_email_only():
    assert mask_account(User(email="ana@gmail.com")) == "a**@gmail.com"


def test_mask_account_nothing():
    assert mask_account(User()) == "—"
    assert mask_account(User(name="  ", email="")) == "—"


def test_mask_account_short_values():
    assert mask_account(User(name="А")) == "А**"
    assert mask_account(User(email="a@x.tj")) == "a**@x.tj"


def test_mask_account_strips_name():
    assert mask_account(User(name="  Ана ")) == "А**"


@pytest.mark.parametrize(
    ("user", "code", "expected"),
    [
        (None, "tg", "tg"),
        (None, "en", "ru"),
        (None, None, "ru"),
        (User(locale="tg"), "ru", "tg"),
        (User(locale="ru"), "tg", "ru"),
        (User(), "tg", "tg"),
    ],
)
def test_bot_lang(user, code, expected):
    assert bot_lang(user, code) == expected


# --- Sign-in service ---------------------------------------------------------------------


def _issue(db, telegram_id=777, first_name="Ана", language_code="tg"):
    return issue_signin(
        db, telegram_id=telegram_id, first_name=first_name, language_code=language_code
    )


def _user_count(db):
    return db.scalar(select(func.count()).select_from(User))


def test_signin_creates_an_account_without_email(db):
    raw = _issue(db)
    outcome, user = sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru")
    assert outcome is Outcome.OK
    assert user.telegram_id == 777
    assert user.email is None
    assert user.name == "Ана"
    assert user.locale == "tg"


def test_signin_uses_the_site_language_for_other_telegram_languages(db):
    raw = _issue(db, language_code="en")
    _, user = sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru")
    assert user.locale == "ru"


def test_signin_finds_the_existing_account_by_telegram_id(db):
    existing = User(email="a@b.tj", name="Old", telegram_id=777)
    db.add(existing)
    db.flush()
    before = _user_count(db)
    outcome, user = sign_in_with_telegram(db, ip="1.1.1.1", raw=_issue(db), lang="ru")
    assert outcome is Outcome.OK
    assert user.id == existing.id
    assert _user_count(db) == before


def test_signin_token_works_once(db):
    raw = _issue(db)
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru")[0] is Outcome.OK
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru") == (Outcome.BAD_LINK, None)


def test_signin_token_expires_after_10_minutes(db):
    assert telegram.SIGNIN_TTL.total_seconds() == 600
    raw = _issue(db)
    db.execute(update(TelegramSignin).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru") == (Outcome.BAD_LINK, None)
    assert signin_preview(db, raw) is None


def test_unknown_token_is_a_bad_link(db):
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw="nope", lang="ru") == (
        Outcome.BAD_LINK,
        None,
    )


def test_preview_does_not_use_the_token(db):
    raw = _issue(db)
    assert signin_preview(db, raw).first_name == "Ана"
    assert signin_preview(db, raw).first_name == "Ана"
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru")[0] is Outcome.OK
    assert signin_preview(db, raw) is None


def test_blocked_account_gets_no_signin_link(db):
    db.add(User(name="B", telegram_id=777, blocked_at=utcnow()))
    db.flush()
    assert _issue(db) is None
    assert db.scalar(select(func.count()).select_from(TelegramSignin)) == 0


def test_account_blocked_after_the_link_was_issued_cannot_sign_in(db):
    user = User(name="B", telegram_id=777)
    db.add(user)
    db.flush()
    raw = _issue(db)
    user.blocked_at = utcnow()
    db.flush()
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru") == (Outcome.BAD_LINK, None)


def test_signin_links_are_limited_per_telegram_user(db):
    for _ in range(5):
        assert _issue(db) is not None
    assert _issue(db) is None
    assert _issue(db, telegram_id=778) is not None


def test_signin_attempts_are_limited_per_ip(db):
    for _ in range(30):
        sign_in_with_telegram(db, ip="9.9.9.9", raw="nope", lang="ru")
    raw = _issue(db)
    assert sign_in_with_telegram(db, ip="9.9.9.9", raw=raw, lang="ru") == (
        Outcome.RATE_LIMITED,
        None,
    )


def test_new_user_name_is_trimmed_and_empty_becomes_none(db):
    _, user = sign_in_with_telegram(
        db, ip="1.1.1.1", raw=_issue(db, first_name="  " + "я" * 100), lang="ru"
    )
    assert user.name == "я" * 80
    _, other = sign_in_with_telegram(
        db, ip="1.1.1.1", raw=_issue(db, telegram_id=5, first_name="   "), lang="ru"
    )
    assert other.name is None


def test_used_or_expired_token_creates_no_account(db):
    used = _issue(db, telegram_id=801)
    sign_in_with_telegram(db, ip="1.1.1.1", raw=used, lang="ru")
    expired = _issue(db, telegram_id=802)
    db.execute(
        update(TelegramSignin)
        .where(TelegramSignin.telegram_id == 802)
        .values(expires_at=utcnow() - timedelta(seconds=1))
    )
    db.execute(delete(User).where(User.telegram_id == 801))
    before = _user_count(db)
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=used, lang="ru")[0] is Outcome.BAD_LINK
    assert sign_in_with_telegram(db, ip="1.1.1.1", raw=expired, lang="ru")[0] is Outcome.BAD_LINK
    assert _user_count(db) == before


def test_long_language_code_is_truncated(db):
    raw = _issue(db, language_code="x" * 20)
    assert raw is not None
    stored = db.scalar(select(TelegramSignin.language_code))
    assert stored == "x" * 8


def test_unknown_site_language_falls_back_to_ru(db):
    _, user = sign_in_with_telegram(db, ip="1.1.1.1", raw=_issue(db, language_code="en"), lang="xx")
    assert user.locale == "ru"
