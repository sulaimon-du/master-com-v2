"""Telegram sign-in, linking and phone verification (spec 2026-11-22).

Sync; never imports aiogram.
"""

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.i18n import LANGS, normalize_lang
from app.core.security import hash_token, new_token
from app.core.time import utcnow
from app.models.auth import TelegramSignin
from app.models.common import PHONE_RE
from app.models.user import User
from app.services import auth, rate_limit

SIGNIN_TTL = timedelta(minutes=10)


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


# --- Sign-in with a one-time link from the bot --------------------------------------------


@dataclass(frozen=True)
class SigninPreview:
    first_name: str


def issue_signin(
    db: Session, *, telegram_id: int, first_name: str, language_code: str | None
) -> str | None:
    """Store a one-time sign-in and return the raw token for the bot to put in the link.

    None when this Telegram user asked too often, or the account behind the ID is blocked or
    deleted (nothing is stored then).
    """
    if not rate_limit.allow(
        db, "tg_signin_issue", str(telegram_id), limit=5, window=auth.LOGIN_WINDOW
    ):
        return None
    owner = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if owner is not None and not auth._is_active(owner):
        return None
    raw = new_token()
    now = utcnow()
    db.add(
        TelegramSignin(
            token_hash=hash_token(raw),
            telegram_id=telegram_id,
            first_name=first_name.strip()[:80],
            language_code=language_code,
            created_at=now,
            expires_at=now + SIGNIN_TTL,
        )
    )
    db.flush()
    return raw


def signin_preview(db: Session, raw: str) -> SigninPreview | None:
    """Who the link is for, without using it. None when unknown, used or expired."""
    first_name = db.scalar(
        select(TelegramSignin.first_name).where(
            TelegramSignin.token_hash == hash_token(raw),
            TelegramSignin.used_at.is_(None),
            TelegramSignin.expires_at > utcnow(),
        )
    )
    return None if first_name is None else SigninPreview(first_name=first_name)


def sign_in_with_telegram(
    db: Session, *, ip: str, raw: str, lang: str
) -> tuple[auth.Outcome, User | None]:
    """Trade the one-time token for the account, creating it on first use.

    The caller opens the session. A blocked or deleted account looks like a bad link.
    """
    if not rate_limit.allow(db, "login_ip", ip, limit=30, window=auth.LOGIN_WINDOW):
        return auth.Outcome.RATE_LIMITED, None
    # Atomic: the link works exactly once, even for two simultaneous requests.
    row = db.execute(
        update(TelegramSignin)
        .where(
            TelegramSignin.token_hash == hash_token(raw),
            TelegramSignin.used_at.is_(None),
            TelegramSignin.expires_at > utcnow(),
        )
        .values(used_at=utcnow())
        .returning(
            TelegramSignin.telegram_id, TelegramSignin.first_name, TelegramSignin.language_code
        )
    ).first()
    if row is None:
        return auth.Outcome.BAD_LINK, None
    telegram_id, first_name, language_code = row

    user = _lock_user(db, telegram_id)
    if user is None:
        try:
            with db.begin_nested():
                user = User(
                    telegram_id=telegram_id,
                    email=None,
                    name=first_name.strip()[:80] or None,
                    locale="tg" if language_code == "tg" else lang,
                )
                db.add(user)
                db.flush()
        except IntegrityError:
            # Another request created this account first: use it.
            user = _lock_user(db, telegram_id)
    if user is None or not auth._is_active(user):
        return auth.Outcome.BAD_LINK, None
    return auth.Outcome.OK, user


def _lock_user(db: Session, telegram_id: int) -> User | None:
    """Reload under the lock: a block committed while we waited must not be missed."""
    return db.scalar(
        select(User)
        .where(User.telegram_id == telegram_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
