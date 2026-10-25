"""Accounts, sessions and one-time links (BRIEF §4.1, R-011).

Routes parse input and render pages. Every rule lives here, so it can be tested without HTTP.
Functions take the raw secret (cookie value or link token) and compare only its hash.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from enum import Enum

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.security import hash_password, hash_token, new_token, verify_password
from app.core.time import utcnow
from app.models.auth import AuthSession, AuthToken, GoogleClaim
from app.models.user import User
from app.services import rate_limit
from app.services.email import EmailSender, OutgoingEmail
from app.services.google_oidc import GoogleIdentity

log = logging.getLogger(__name__)

SESSION_IDLE = timedelta(days=30)
SESSION_ABSOLUTE = timedelta(days=90)
SESSION_TOUCH_EVERY = timedelta(hours=1)
CONFIRM_TTL = timedelta(hours=48)
RESET_TTL = timedelta(hours=1)
# An owner's decision on a Google claim counts for 30 days; after that a new request is needed.
GOOGLE_CLAIM_TTL = timedelta(days=30)
HOUR = timedelta(hours=1)
LOGIN_WINDOW = timedelta(minutes=15)

# Builds an absolute URL from a route name and query parameters.
UrlFor = Callable[[str, dict[str, str]], str]


class Outcome(Enum):
    OK = "ok"
    SENT = "sent"
    WRONG_CREDENTIALS = "wrong_credentials"
    RATE_LIMITED = "rate_limited"
    BAD_LINK = "invalid_token"
    EMAIL_TAKEN = "email_taken"
    GOOGLE_ALREADY_LINKED = "google_already_linked"
    GOOGLE_TAKEN = "google_taken"
    CLAIM_PENDING = "claim_pending"
    CLAIM_REJECTED = "claim_rejected"


def _is_active(user: User) -> bool:
    return user.blocked_at is None and user.deleted_at is None


def _send(sender: EmailSender, to: str, lang: str, key: str, **params: str) -> None:
    sender.send(
        OutgoingEmail(
            to=to,
            subject=translate(lang, f"email.{key}.subject"),
            body=translate(lang, f"email.{key}.body", **params),
        )
    )


def _issue_token(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    raw = new_token()
    now = utcnow()
    db.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            created_at=now,
            expires_at=now + ttl,
        )
    )
    db.flush()
    return raw


def _consume_token(db: Session, purpose: str, raw: str) -> User | None:
    """Mark the token used and return its user. Atomic, so a link works exactly once."""
    row = db.execute(
        update(AuthToken)
        .where(
            AuthToken.token_hash == hash_token(raw),
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
        .values(used_at=utcnow())
        .returning(AuthToken.user_id)
    ).first()
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None or not _is_active(user):
        return None
    return user


def _burn_tokens(db: Session, user_id: int, purpose: str) -> None:
    """Use up every other open link of this kind, so an older email cannot be replayed later."""
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )


# --- Registration and email confirmation --------------------------------------------------


def register(
    db: Session,
    sender: EmailSender,
    *,
    ip: str,
    name: str,
    email: str,
    password: str,
    lang: str,
    url_for: UrlFor,
) -> Outcome:
    """Create an unconfirmed account and send the confirmation link.

    An existing address gets a reminder instead, and the answer is the same: the caller cannot
    tell the two cases apart (BRIEF §4.1, no account enumeration).
    """
    if not rate_limit.allow(db, "register_ip", ip, limit=10, window=HOUR):
        return Outcome.RATE_LIMITED
    # Hash in both branches, so the response time does not reveal whether the address exists.
    password_hash = hash_password(password)
    # At most 3 emails per address per hour, whichever kind. Over the limit nothing is sent,
    # but the answer is the same, so a stranger cannot flood someone's inbox or learn anything.
    may_email = rate_limit.allow(db, "register_email", email, limit=3, window=HOUR)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        if not may_email:
            return Outcome.SENT
        _send(
            sender,
            email,
            lang,
            "exists",
            signin_url=url_for("signin_page", {}),
            reset_url=url_for("forgot_page", {}),
        )
        return Outcome.SENT

    try:
        with db.begin_nested():
            user = User(email=email, name=name, password_hash=password_hash, locale=lang)
            db.add(user)
            db.flush()
    except IntegrityError:
        # Another request registered the same address first: answer as for an existing one.
        if not may_email:
            return Outcome.SENT
        _send(
            sender,
            email,
            lang,
            "exists",
            signin_url=url_for("signin_page", {}),
            reset_url=url_for("forgot_page", {}),
        )
        return Outcome.SENT

    raw = _issue_token(db, user, "confirm_email", CONFIRM_TTL)
    _send(sender, email, lang, "confirm", url=url_for("confirm_page", {"token": raw}))
    return Outcome.SENT


def resend_confirmation(
    db: Session,
    sender: EmailSender,
    *,
    ip: str,
    email: str,
    lang: str,
    url_for: UrlFor,
) -> Outcome:
    if not rate_limit.allow(db, "confirm_ip", ip, limit=20, window=HOUR):
        return Outcome.RATE_LIMITED
    # Over the per-address limit nothing is sent, but the answer stays the same: a visible 429
    # here would tell a stranger whether this address had just received emails.
    may_email = rate_limit.allow(db, "confirm_email", email, limit=3, window=HOUR)
    user = db.scalar(select(User).where(User.email == email))
    if may_email and user is not None and _is_active(user) and user.email_verified_at is None:
        raw = _issue_token(db, user, "confirm_email", CONFIRM_TTL)
        _send(sender, email, user.locale, "confirm", url=url_for("confirm_page", {"token": raw}))
    return Outcome.SENT


def confirm_email(db: Session, *, ip: str, raw: str, password: str) -> tuple[Outcome, User | None]:
    """Confirm the address. The link proves the inbox; the password proves who registered.

    Without the password check, a stranger could register someone's address with their own
    password, wait for the owner to confirm it from the inbox, and then sign in (pre-hijack).
    A wrong password does not use up the link. An owner who does not know the password resets
    it instead, which also confirms the address and removes the stranger's password.
    """
    if not rate_limit.allow(db, "confirm_attempt_ip", ip, limit=30, window=LOGIN_WINDOW):
        return Outcome.RATE_LIMITED, None
    token = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == hash_token(raw),
            AuthToken.purpose == "confirm_email",
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > utcnow(),
        )
    )
    user = db.get(User, token.user_id) if token else None
    if user is None or not _is_active(user):
        return Outcome.BAD_LINK, None
    # A valid link is a way to try passwords: share the sign-in limit for this address. Only a
    # link holder gets this far, so it reveals nothing to strangers.
    if not rate_limit.allow(db, "login_email", user.email or "", limit=5, window=LOGIN_WINDOW):
        return Outcome.RATE_LIMITED, None
    if not verify_password(user.password_hash, password):
        return Outcome.WRONG_CREDENTIALS, None
    if _consume_token(db, "confirm_email", raw) is None:
        return Outcome.BAD_LINK, None
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    _burn_tokens(db, user.id, "confirm_email")
    return Outcome.OK, user


# --- Password sign-in ---------------------------------------------------------------------


def sign_in_with_password(
    db: Session,
    sender: EmailSender,
    *,
    ip: str,
    email: str,
    password: str,
    url_for: UrlFor,
) -> tuple[Outcome, User | None]:
    """Check the password. Every failure, including "not confirmed yet", looks the same.

    A separate "confirm your email" answer would let anyone test an address: register it with a
    known password, then sign in and see which answer comes back. Instead an unconfirmed owner
    with the right password quietly gets a fresh confirmation link (at most 3 per hour, on its
    own counter), and the generic message tells them to check their inbox.
    """
    if not rate_limit.allow(db, "login_ip", ip, limit=30, window=LOGIN_WINDOW):
        return Outcome.RATE_LIMITED, None
    if not rate_limit.allow(db, "login_email", email, limit=5, window=LOGIN_WINDOW):
        return Outcome.RATE_LIMITED, None

    user = db.scalar(select(User).where(User.email == email))
    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or not password_ok or not _is_active(user):
        return Outcome.WRONG_CREDENTIALS, None
    if user.email_verified_at is None:
        # Own counter, never shown as 429: sharing one with "resend" would make it observable.
        if rate_limit.allow(db, "signin_confirm", email, limit=3, window=HOUR):
            raw = _issue_token(db, user, "confirm_email", CONFIRM_TTL)
            _send(
                sender, email, user.locale, "confirm", url=url_for("confirm_page", {"token": raw})
            )
        return Outcome.WRONG_CREDENTIALS, None
    return Outcome.OK, user


# --- Sessions -----------------------------------------------------------------------------


def open_session(db: Session, user: User, *, replace: str | None = None) -> str:
    """Start a session and return its cookie value. `replace` is the old cookie, deleted here.

    Called at every sign-in and privilege change, so a session ID fixed before sign-in is useless
    afterwards (R-011).
    """
    if replace is not None:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(replace)))
    raw = new_token()
    now = utcnow()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            last_seen_at=now,
            expires_at=now + SESSION_ABSOLUTE,
        )
    )
    db.flush()
    return raw


def user_for_session(db: Session, raw: str | None) -> User | None:
    if not raw:
        return None
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
    if row is None:
        return None
    now = utcnow()
    user = db.get(User, row.user_id)
    if user is None or not _is_active(user) or row.expires_at <= now:
        db.delete(row)
        return None
    if row.last_seen_at <= now - SESSION_IDLE:
        db.delete(row)
        return None
    if now - row.last_seen_at >= SESSION_TOUCH_EVERY:
        row.last_seen_at = now
    return user


def end_session(db: Session, raw: str | None) -> None:
    if raw:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(raw)))


def revoke_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


def block_user(db: Session, user: User) -> None:
    """Block an account and end all its sessions at once (BRIEF §4.1). Admin UI comes in week 10."""
    user.blocked_at = utcnow()
    revoke_all_sessions(db, user.id)


# --- Password reset -----------------------------------------------------------------------


def request_password_reset(
    db: Session,
    sender: EmailSender,
    *,
    ip: str,
    email: str,
    lang: str,
    url_for: UrlFor,
) -> Outcome:
    if not rate_limit.allow(db, "reset_ip", ip, limit=20, window=HOUR):
        return Outcome.RATE_LIMITED
    if not rate_limit.allow(db, "reset_email", email, limit=3, window=HOUR):
        return Outcome.RATE_LIMITED
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and _is_active(user):
        raw = _issue_token(db, user, "reset_password", RESET_TTL)
        _send(sender, email, user.locale, "reset", url=url_for("reset_page", {"token": raw}))
    return Outcome.SENT


def reset_password(db: Session, *, ip: str, raw: str, password: str) -> Outcome:
    if not rate_limit.allow(db, "reset_attempt_ip", ip, limit=30, window=HOUR):
        return Outcome.RATE_LIMITED
    user = _consume_token(db, "reset_password", raw)
    if user is None:
        return Outcome.BAD_LINK
    user.password_hash = hash_password(password)
    # Following the link proves the inbox, so the address counts as confirmed.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    # Older links in the inbox stop working too: a confirmation link plus the new password
    # would otherwise be a second way in.
    _burn_tokens(db, user.id, "reset_password")
    _burn_tokens(db, user.id, "confirm_email")
    # A new password is a privilege change: every existing session ends.
    revoke_all_sessions(db, user.id)
    return Outcome.OK


# --- Google sign-in and linking -----------------------------------------------------------


def allow_google_start(db: Session, *, ip: str) -> bool:
    """Each start stores a flow row: cap it per address so a bot cannot fill the table."""
    return rate_limit.allow(db, "google_start_ip", ip, limit=30, window=LOGIN_WINDOW)


def sign_in_with_google(
    db: Session, identity: GoogleIdentity, *, lang: str
) -> tuple[Outcome, User | None]:
    """Sign in by Google `sub`. A confirmed account with the same email is refused, never merged."""
    user = db.scalar(select(User).where(User.google_sub == identity.sub))
    if user is not None:
        if not _is_active(user):
            return Outcome.WRONG_CREDENTIALS, None
        return Outcome.OK, user
    # Locked until commit: a confirmation or reset racing with a handover waits, then the
    # handover sees the confirmed account and refuses.
    existing = db.scalar(select(User).where(User.email == identity.email).with_for_update())
    if existing is not None:
        if not _is_active(existing):
            return Outcome.WRONG_CREDENTIALS, None
        if not claimable(existing):
            # The account is proven some other way: sign in with it first, then link Google.
            return Outcome.EMAIL_TAKEN, None
        # Nobody ever proved this address, and Google just did. The unconfirmed account may be a
        # stranger's squat, but the handover still waits for the owner's approval (R-019).
        decided = db.scalars(
            select(GoogleClaim.status).where(
                GoogleClaim.user_id == existing.id,
                GoogleClaim.google_sub == identity.sub,
                GoogleClaim.email == existing.email,
                GoogleClaim.status != "pending",
                GoogleClaim.resolved_at > utcnow() - GOOGLE_CLAIM_TTL,
            )
        ).all()
        if "rejected" in decided:
            # A refusal sticks: no new request, the page points to password reset instead.
            return Outcome.CLAIM_REJECTED, None
        if "approved" in decided:
            return _hand_over_to_google(db, existing, identity, lang=lang)
        _open_google_claim(db, existing, identity)
        return Outcome.CLAIM_PENDING, None

    now = utcnow()
    try:
        with db.begin_nested():
            user = User(
                email=identity.email,
                email_verified_at=now,
                google_sub=identity.sub,
                name=(identity.name or "")[:80] or None,
                locale=lang,
            )
            db.add(user)
            db.flush()
    except IntegrityError:
        return Outcome.EMAIL_TAKEN, None
    return Outcome.OK, user


def claimable(user: User) -> bool:
    """Nobody has proven this account: no confirmed email and no other sign-in method."""
    return (
        user.email_verified_at is None
        and user.google_sub is None
        and user.telegram_id is None
        and user.phone_verified_at is None
    )


def _open_google_claim(db: Session, user: User, identity: GoogleIdentity) -> None:
    """Record the request once; asking again while it is pending adds nothing."""
    try:
        with db.begin_nested():
            db.add(
                GoogleClaim(
                    user_id=user.id,
                    email=user.email or "",
                    google_sub=identity.sub,
                    google_name=(identity.name or "")[:80] or None,
                )
            )
            db.flush()
    except IntegrityError:
        return
    log.warning("google claim pending for user id=%s", user.id)


def _hand_over_to_google(
    db: Session, user: User, identity: GoogleIdentity, *, lang: str
) -> tuple[Outcome, User | None]:
    """Approved claim: drop the unproven password, its links and sessions; Google signs in."""
    try:
        with db.begin_nested():
            user.password_hash = None
            user.google_sub = identity.sub
            user.email_verified_at = utcnow()
            user.name = (identity.name or "")[:80] or None
            user.locale = lang
            db.flush()
    except IntegrityError:
        return Outcome.EMAIL_TAKEN, None
    _burn_tokens(db, user.id, "confirm_email")
    _burn_tokens(db, user.id, "reset_password")
    revoke_all_sessions(db, user.id)
    return Outcome.OK, user


def pending_google_claims(db: Session) -> list[GoogleClaim]:
    return list(
        db.scalars(
            select(GoogleClaim)
            .where(GoogleClaim.status == "pending")
            .order_by(GoogleClaim.created_at)
        )
    )


def resolve_google_claim(
    db: Session, claim_id: int, *, approve: bool
) -> tuple[Outcome, GoogleClaim | None]:
    """Owner's decision on a pending claim. Approval takes effect at the next Google sign-in."""
    claim = db.get(GoogleClaim, claim_id)
    if claim is None or claim.status != "pending":
        return Outcome.BAD_LINK, None
    claim.status = "approved" if approve else "rejected"
    claim.resolved_at = utcnow()
    return Outcome.OK, claim


def link_google(db: Session, user: User, identity: GoogleIdentity) -> Outcome:
    """Attach a Google account to a signed-in user. Only from settings, never on sign-in."""
    if user.google_sub is not None:
        return Outcome.GOOGLE_ALREADY_LINKED
    taken = db.scalar(select(User.id).where(User.google_sub == identity.sub))
    if taken is not None:
        return Outcome.GOOGLE_TAKEN
    try:
        with db.begin_nested():
            user.google_sub = identity.sub
            db.flush()
    except IntegrityError:
        return Outcome.GOOGLE_TAKEN
    return Outcome.OK
