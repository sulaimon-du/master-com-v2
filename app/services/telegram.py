"""Telegram sign-in, linking and phone verification (spec 2026-11-22).

Sync; never imports aiogram.
"""

import re

from app.core.i18n import LANGS, normalize_lang
from app.models.common import PHONE_RE
from app.models.user import User


def normalize_phone(raw: str) -> str | None:
    """Return `+<digits>` in E.164 shape, or None for anything else.

    Spaces, dashes, dots and parentheses are ignored; any other character
    (letters, a second `+`) makes the input invalid.
    """
    cleaned = raw.strip()
    if re.search(r"[^\d\s\-.()+]", cleaned) or cleaned.count("+") > (
        1 if cleaned.startswith("+") else 0
    ):
        return None
    phone = "+" + re.sub(r"\D", "", cleaned)
    return phone if re.fullmatch(PHONE_RE, phone) else None


def mask_account(user: User) -> str:
    """Mask name and email to an initial plus `**`; `—` when the account has neither."""
    parts: list[str] = []
    name = (user.name or "").strip()
    if name:
        parts.append(f"{name[0]}**")
    if user.email and "@" in user.email:
        local, _, domain = user.email.partition("@")
        if local:
            parts.append(f"{local[0]}**@{domain}")
    return " · ".join(parts) or "—"


def bot_lang(user: User | None, language_code: str | None) -> str:
    """Account locale if there is one, else Telegram's `language_code`, else ru."""
    if user is not None and user.locale in LANGS:
        return user.locale
    return normalize_lang(language_code)
