from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import IdPk, one_of

TOKEN_PURPOSES = ("confirm_email", "reset_password")


class AuthSession(IdPk, Base):
    """A signed-in browser. The cookie carries a random ID; only its SHA-256 is stored (R-011)."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Idle timeout (30 days) is measured from here; updated at most once an hour.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Absolute lifetime (90 days from sign-in).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthToken(IdPk, Base):
    """One-time link for email confirmation or password reset. Single use, expiring, hashed."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint(one_of("purpose", TOKEN_PURPOSES), name="purpose"),)


class RateLimit(Base):
    """Fixed-window counter in Postgres (BRIEF §6.3). The key is a hash of scope and identity."""

    __tablename__ = "rate_limits"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer)


class OAuthFlow(IdPk, Base):
    """State of one Google sign-in or link, between the redirect and the callback.

    Used once: the callback deletes the row. The browser holds the same `state` in a cookie.
    """

    __tablename__ = "oauth_flows"

    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    # Set for "link Google to my account"; null for sign-in.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
