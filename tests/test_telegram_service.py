from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, update

from app.core.time import utcnow
from app.models.auth import AuthToken, TelegramSignin
from app.models.user import User
from app.services import auth, telegram
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


def test_signin_first_sign_in_race_uses_the_existing_account(db, monkeypatch):
    # The unlocked lookup misses the account another request has just created: the insert hits
    # the unique index, and the re-select finds the account. No error, same user.
    existing = _web_user(db, telegram_id=777)
    raw = _issue(db)
    before = _user_count(db)
    real = telegram._lock_user
    calls = []

    def lock_once_blind(db_, telegram_id):
        calls.append(telegram_id)
        return None if len(calls) == 1 else real(db_, telegram_id)

    monkeypatch.setattr(telegram, "_lock_user", lock_once_blind)
    outcome, user = sign_in_with_telegram(db, ip="1.1.1.1", raw=raw, lang="ru")
    assert outcome is Outcome.OK
    assert user is existing
    assert len(calls) == 2
    assert _user_count(db) == before


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


# --- Linking and phone service -----------------------------------------------------------


def _web_user(db, **kw):
    user = User(email=kw.pop("email", "a@b.tj"), name="Ана", **kw)
    db.add(user)
    db.flush()
    return user


def _link_token_id(db, user):
    raw = telegram.issue_link(db, user)
    preview = telegram.link_preview(db, raw)
    return raw, preview.token_id


def test_confirm_link_attaches_telegram_to_the_token_owner(db):
    user = _web_user(db)
    raw, token_id = _link_token_id(db, user)
    assert telegram.link_preview(db, raw).account == "А** · a**@b.tj"
    outcome, linked = telegram.confirm_link(db, token_id=token_id, telegram_id=777)
    assert outcome is Outcome.OK
    assert linked.id == user.id
    assert user.telegram_id == 777


def test_link_preview_does_not_use_the_token(db):
    raw, token_id = _link_token_id(db, _web_user(db))
    assert telegram.link_preview(db, raw) is not None
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777)[0] is Outcome.OK


def test_link_preview_unknown_token_is_none(db):
    assert telegram.link_preview(db, "nope") is None


def test_link_token_works_once(db):
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777)[0] is Outcome.OK
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.BAD_LINK,
        None,
    )


def test_second_confirm_click_is_bad_link_not_error(db):
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    telegram.confirm_link(db, token_id=token_id, telegram_id=777)
    outcome, linked = telegram.confirm_link(db, token_id=token_id, telegram_id=777)
    assert (outcome, linked) == (Outcome.BAD_LINK, None)
    assert user.telegram_id == 777


def test_expired_or_unknown_link_token_is_bad_link(db):
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    db.execute(update(AuthToken).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777)[0] is Outcome.BAD_LINK
    assert telegram.confirm_link(db, token_id=987654321, telegram_id=777)[0] is Outcome.BAD_LINK


def test_other_purpose_token_cannot_be_confirmed_as_a_link(db):
    user = _web_user(db)
    auth._issue_token(db, user, "confirm_email", timedelta(hours=1))
    token_id = db.scalar(select(AuthToken.id).where(AuthToken.purpose == "confirm_email"))
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777)[0] is Outcome.BAD_LINK
    assert user.telegram_id is None


def test_callback_data_round_trip(db):
    data = telegram.link_callback_data(42, 777)
    assert data.startswith("l:42:")
    assert len(data.encode()) <= 64
    assert telegram.token_id_from_callback(data, 777) == 42


def test_callback_data_from_another_telegram_user_is_rejected():
    data = telegram.link_callback_data(42, 777)
    assert telegram.token_id_from_callback(data, 888) is None


@pytest.mark.parametrize(
    "data",
    [
        "",
        "x",
        "l:",
        "l:42",
        "l:42:",
        "l:abc:0123456789abcdef",
        "l:42:short",
        "x:42:0123456789abcdef",
        "l:42:0123456789abcdef",
        "l:42:" + "g" * 16,
        "l:42:0123456789abcdef:extra",
        "l:-1:0123456789abcdef",
        "l:" + "1" * 5000 + ":" + "a" * 16,
        "l:" + "1" * 20 + ":" + "a" * 16,
        "l:" + "9" * 19 + ":" + "a" * 16,
    ],
)
def test_malformed_callback_data_is_none(data):
    assert telegram.token_id_from_callback(data, 777) is None


def test_telegram_linked_to_another_account_is_refused(db):
    _web_user(db, email="x@b.tj", telegram_id=777)
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.TELEGRAM_TAKEN,
        None,
    )
    assert user.telegram_id is None


def test_account_with_a_different_telegram_is_refused(db):
    user = _web_user(db, telegram_id=555)
    _, token_id = _link_token_id(db, user)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.TELEGRAM_OTHER_LINKED,
        None,
    )
    assert user.telegram_id == 555


def test_same_telegram_again_is_ok(db):
    user = _web_user(db, telegram_id=777)
    _, token_id = _link_token_id(db, user)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777)[0] is Outcome.OK


def test_blocked_account_cannot_be_linked(db):
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    user.blocked_at = utcnow()
    db.flush()
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.BAD_LINK,
        None,
    )
    assert user.telegram_id is None


def test_confirm_link_is_limited_per_telegram_user(db):
    for _ in range(20):
        telegram.confirm_link(db, token_id=1, telegram_id=777)
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.RATE_LIMITED,
        None,
    )


def test_link_start_is_limited_per_user_and_per_ip(db):
    for _ in range(10):
        assert telegram.allow_link_start(db, user_id=1, ip="1.1.1.1")
    assert not telegram.allow_link_start(db, user_id=1, ip="2.2.2.2")
    for i in range(30):
        assert telegram.allow_link_start(db, user_id=100 + i, ip="3.3.3.3")
    assert not telegram.allow_link_start(db, user_id=999, ip="3.3.3.3")


def _verify(db, telegram_id=777, contact=777, phone="992901234567"):
    return telegram.verify_phone(
        db, telegram_id=telegram_id, contact_user_id=contact, phone_number=phone
    )


def test_own_contact_verifies_the_phone(db):
    user = _web_user(db, telegram_id=777)
    assert _verify(db) is Outcome.OK
    assert user.phone == "+992901234567"
    assert user.phone_verified_at is not None


def test_someone_elses_contact_is_refused(db):
    user = _web_user(db, telegram_id=777)
    assert _verify(db, contact=999) is Outcome.NOT_OWN_CONTACT
    assert user.phone is None


def test_contact_without_user_id_is_refused(db):
    user = _web_user(db, telegram_id=777)
    assert _verify(db, contact=None) is Outcome.NOT_OWN_CONTACT
    assert user.phone is None


def test_unlinked_telegram_cannot_verify(db):
    assert _verify(db) is Outcome.NOT_LINKED


def test_blocked_account_cannot_verify(db):
    user = _web_user(db, telegram_id=777, blocked_at=utcnow())
    assert _verify(db) is Outcome.NOT_LINKED
    assert user.phone is None


def test_unparsable_phone_is_invalid(db):
    user = _web_user(db, telegram_id=777)
    assert _verify(db, phone="abc") is Outcome.PHONE_INVALID
    assert user.phone is None


def test_phone_verified_elsewhere_is_refused(db):
    _web_user(db, email="x@b.tj", phone="+992901234567", phone_verified_at=utcnow())
    user = _web_user(db, telegram_id=777)
    assert _verify(db) is Outcome.PHONE_TAKEN
    assert user.phone is None


def test_phone_unverified_elsewhere_does_not_block(db):
    _web_user(db, email="x@b.tj", phone="+992901234567")
    user = _web_user(db, telegram_id=777)
    assert _verify(db) is Outcome.OK
    assert user.phone_verified_at is not None


def test_changing_a_verified_phone_needs_support(db):
    user = _web_user(db, telegram_id=777, phone="+992900000001", phone_verified_at=utcnow())
    assert _verify(db) is Outcome.PHONE_CHANGE_NEEDS_SUPPORT
    assert user.phone == "+992900000001"


def test_same_phone_again_is_already(db):
    _web_user(db, telegram_id=777, phone="+992901234567", phone_verified_at=utcnow())
    assert _verify(db) is Outcome.ALREADY


def test_unverified_own_phone_gets_verified(db):
    user = _web_user(db, telegram_id=777, phone="+992900000001")
    assert _verify(db) is Outcome.OK
    assert user.phone == "+992901234567"
    assert user.phone_verified_at is not None


def test_phone_taken_race_is_phone_taken_not_error(db, monkeypatch):
    # The pre-check sees nothing, the unique index catches the other account.
    _web_user(db, email="x@b.tj", phone="+992901234567", phone_verified_at=utcnow())
    user = _web_user(db, telegram_id=777)
    monkeypatch.setattr(telegram, "_phone_verified_elsewhere", lambda *a, **k: False)
    assert _verify(db) is Outcome.PHONE_TAKEN
    assert user.phone is None
    assert user.phone_verified_at is None


def test_verify_phone_is_limited_per_telegram_user(db):
    _web_user(db, telegram_id=777)
    for _ in range(20):
        _verify(db, contact=999)
    assert _verify(db) is Outcome.RATE_LIMITED


def test_telegram_taken_race_is_telegram_taken_not_error(db, monkeypatch):
    # The pre-check misses, the unique index catches the account that holds the ID.
    _web_user(db, email="x@b.tj", telegram_id=777)
    user = _web_user(db)
    _, token_id = _link_token_id(db, user)
    monkeypatch.setattr(telegram, "_telegram_taken", lambda *a, **k: False)
    assert telegram.confirm_link(db, token_id=token_id, telegram_id=777) == (
        Outcome.TELEGRAM_TAKEN,
        None,
    )
    assert user.telegram_id is None


def test_user_by_telegram_id(db):
    user = User(name="A", telegram_id=4242)
    db.add(user)
    db.flush()
    assert telegram.user_by_telegram_id(db, 4242) is user
    assert telegram.user_by_telegram_id(db, 4243) is None
