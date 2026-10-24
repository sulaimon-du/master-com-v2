from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import LANGS, IdPk, Timestamps, one_of, phone_ok


class User(IdPk, Timestamps, Base):
    """An account. Client by default; a master when it owns a MasterProfile (BRIEF §3).

    Sign-in methods are optional columns: a Telegram-only account has no email (BRIEF §4.1).
    Account deletion clears personal columns and sets `deleted_at`; the row stays so that
    reviews and chat history keep their links ("Deleted user", R-011, PRODUCT_RULES C8).
    """

    __tablename__ = "users"

    # Stored lowercased; uniqueness is case-insensitive because of the CHECK below.
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)

    name: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(16))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locale: Mapped[str] = mapped_column(String(2), server_default="ru")

    is_admin: Mapped[bool] = mapped_column(server_default=false())
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(phone_ok("phone"), name="phone_format"),
        CheckConstraint(
            "phone_verified_at IS NULL OR phone IS NOT NULL", name="verified_phone_present"
        ),
        CheckConstraint(one_of("locale", LANGS), name="locale"),
        # A phone number can be verified by one account only; unverified duplicates are fine.
        Index(
            "uq_users_verified_phone",
            "phone",
            unique=True,
            postgresql_where=text("phone_verified_at IS NOT NULL"),
        ),
    )
