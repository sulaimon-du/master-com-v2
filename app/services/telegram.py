"""Telegram sign-in, linking and phone verification (spec 2026-11-22).

Sync; never imports aiogram.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.i18n import LANGS, normalize_lang
from app.core.security import hash_token, new_token
from app.core.time import utcnow
from app.models.auth import AuthToken, TelegramSignin
from app.models.common import PHONE_RE
from app.models.user import User
from app.services import auth, rate_limit

SIGNIN_TTL = timedelta(minutes=10)
LINK_TTL = timedelta(minutes=30)


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
    if owner is not None and not auth.is_active(owner):
        return None
    raw = new_token()
    now = utcnow()
    db.add(
        TelegramSignin(
            token_hash=hash_token(raw),
            telegram_id=telegram_id,
            first_name=first_name.strip()[:80],
            language_code=(language_code or "")[:8] or None,
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
    token_hash = hash_token(raw)
    # Lock the user before the token row, as `auth._consume_token` does, so the two cannot
    # deadlock. The first read is unlocked: it only finds out whose link this is.
    telegram_id = db.scalar(
        select(TelegramSignin.telegram_id).where(TelegramSignin.token_hash == token_hash)
    )
    if telegram_id is None:
        return auth.Outcome.BAD_LINK, None
    user = _lock_user(db, telegram_id)
    # Atomic: the link works exactly once, even for two simultaneous requests.
    row = db.execute(
        update(TelegramSignin)
        .where(
            TelegramSignin.token_hash == token_hash,
            TelegramSignin.used_at.is_(None),
            TelegramSignin.expires_at > utcnow(),
        )
        .values(used_at=utcnow())
        .returning(TelegramSignin.first_name, TelegramSignin.language_code)
    ).first()
    if row is None or (user is not None and not auth.is_active(user)):
        return auth.Outcome.BAD_LINK, None
    if user is None:
        first_name, language_code = row
        try:
            with db.begin_nested():
                user = User(
                    telegram_id=telegram_id,
                    email=None,
                    name=first_name.strip()[:80] or None,
                    locale="tg" if language_code == "tg" else normalize_lang(lang),
                )
                db.add(user)
                db.flush()
        except IntegrityError:
            # Another request created this account first: use it.
            user = _lock_user(db, telegram_id)
        if user is None or not auth.is_active(user):
            return auth.Outcome.BAD_LINK, None
    return auth.Outcome.OK, user


def user_by_telegram_id(db: Session, telegram_id: int) -> User | None:
    """The account bound to this Telegram user, or None. Read only, no lock."""
    return db.scalar(select(User).where(User.telegram_id == telegram_id))


def clear_bot_blocked(db: Session, telegram_id: int) -> None:
    """The Telegram user wrote to the bot: they no longer block it (spec D10.6)."""
    db.execute(
        update(User)
        .where(User.telegram_id == telegram_id, User.telegram_blocked_at.is_not(None))
        .values(telegram_blocked_at=None)
    )


def _lock_user(db: Session, telegram_id: int) -> User | None:
    """Reload under the lock: a block committed while we waited must not be missed."""
    return db.scalar(
        select(User)
        .where(User.telegram_id == telegram_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


# --- Linking Telegram to a site account ---------------------------------------------------


@dataclass(frozen=True)
class LinkPreview:
    token_id: int
    account: str  # masked name and email


def allow_link_start(db: Session, *, user_id: int, ip: str) -> bool:
    """Site "Link Telegram" / "Verify phone": 10 per user per hour, 30 per IP per 15 minutes."""
    user_ok = rate_limit.allow(db, "tg_link_user", str(user_id), limit=10, window=auth.HOUR)
    ip_ok = rate_limit.allow(db, "tg_link_ip", ip, limit=30, window=auth.LOGIN_WINDOW)
    return user_ok and ip_ok


def issue_link(db: Session, user: User) -> str:
    """One-time token that binds a Telegram user to `user` once they confirm in the bot."""
    return auth._issue_token(db, user, "tg_link", LINK_TTL)


def link_preview(db: Session, raw: str) -> LinkPreview | None:
    """Which account the link is for, without using it. None when unknown, used or expired."""
    row = db.execute(
        select(AuthToken.id, User)
        .join(User, User.id == AuthToken.user_id)
        .where(
            AuthToken.token_hash == hash_token(raw),
            AuthToken.purpose == "tg_link",
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
    ).first()
    if row is None or not auth.is_active(row[1]):
        return None
    return LinkPreview(token_id=row[0], account=mask_account(row[1]))


def _callback_mac(token_id: int, telegram_id: int) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    msg = f"{token_id}:{telegram_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]


def link_callback_data(token_id: int, telegram_id: int) -> str:
    """Callback data of the "Yes" button. Bound to the Telegram user it was shown to."""
    return f"l:{token_id}:{_callback_mac(token_id, telegram_id)}"


def token_id_from_callback(data: str, telegram_id: int) -> int | None:
    """The token ID in valid callback data for this Telegram user, else None. Never raises."""
    if len(data) > 64:  # Telegram's callback_data cap; also keeps int() cheap
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "l":
        return None
    _, raw_id, mac = parts
    if not (raw_id.isascii() and raw_id.isdigit()) or len(mac) != 16 or len(raw_id) > 18:
        return None
    token_id = int(raw_id)
    if not hmac.compare_digest(mac.encode(), _callback_mac(token_id, telegram_id).encode()):
        return None
    return token_id


def _telegram_taken(db: Session, telegram_id: int) -> bool:
    return db.scalar(select(User.id).where(User.telegram_id == telegram_id)) is not None


def confirm_link(
    db: Session, *, token_id: int, telegram_id: int
) -> tuple[auth.Outcome, User | None]:
    """Bind `telegram_id` to the account the link token belongs to. The token works once."""
    if not rate_limit.allow(db, "tg_action", str(telegram_id), limit=20, window=auth.HOUR):
        return auth.Outcome.RATE_LIMITED, None
    # Lock the user before the token row, as `auth._consume_token` does. The first read is
    # unlocked: it only finds out whose token this is.
    owner = db.scalar(
        select(AuthToken.user_id).where(AuthToken.id == token_id, AuthToken.purpose == "tg_link")
    )
    if owner is None:
        return auth.Outcome.BAD_LINK, None
    user = db.scalar(
        select(User)
        .where(User.id == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = db.execute(
        update(AuthToken)
        .where(
            AuthToken.id == token_id,
            AuthToken.purpose == "tg_link",
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
        .values(used_at=utcnow())
        .returning(AuthToken.user_id)
    ).first()
    if row is None or user is None or not auth.is_active(user):
        return auth.Outcome.BAD_LINK, None
    if user.telegram_id == telegram_id:
        return auth.Outcome.OK, user
    if user.telegram_id is not None:
        return auth.Outcome.TELEGRAM_OTHER_LINKED, None
    if _telegram_taken(db, telegram_id):
        return auth.Outcome.TELEGRAM_TAKEN, None
    try:
        with db.begin_nested():
            user.telegram_id = telegram_id
            db.flush()
    except IntegrityError:
        db.refresh(user)
        return auth.Outcome.TELEGRAM_TAKEN, None
    return auth.Outcome.OK, user


# --- Phone verification through the bot's contact button ----------------------------------


def _phone_verified_elsewhere(db: Session, phone: str, user_id: int) -> bool:
    return (
        db.scalar(
            select(User.id).where(
                User.phone == phone, User.phone_verified_at.is_not(None), User.id != user_id
            )
        )
        is not None
    )


def verify_phone(
    db: Session, *, telegram_id: int, contact_user_id: int | None, phone_number: str
) -> auth.Outcome:
    """Save the phone a Telegram user shared with the contact button, as verified.

    Only the user's own contact counts. A verified phone is never replaced here: changing it
    is a support action.
    """
    if not rate_limit.allow(db, "tg_action", str(telegram_id), limit=20, window=auth.HOUR):
        return auth.Outcome.RATE_LIMITED
    if contact_user_id is None or contact_user_id != telegram_id:
        return auth.Outcome.NOT_OWN_CONTACT
    user = _lock_user(db, telegram_id)
    if user is None or not auth.is_active(user):
        return auth.Outcome.NOT_LINKED
    phone = normalize_phone(phone_number)
    if phone is None:
        return auth.Outcome.PHONE_INVALID
    if user.phone_verified_at is not None:
        return (
            auth.Outcome.ALREADY if user.phone == phone else auth.Outcome.PHONE_CHANGE_NEEDS_SUPPORT
        )
    if _phone_verified_elsewhere(db, phone, user.id):
        return auth.Outcome.PHONE_TAKEN
    try:
        with db.begin_nested():
            user.phone = phone
            user.phone_verified_at = utcnow()
            db.flush()
    except IntegrityError:
        db.refresh(user)
        return auth.Outcome.PHONE_TAKEN
    return auth.Outcome.OK
