import pytest

from app.models.user import User
from app.services.telegram import bot_lang, mask_account, normalize_phone


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
