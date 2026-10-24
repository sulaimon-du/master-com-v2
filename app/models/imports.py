"""Imported profiles: claiming, "Remove my data" and the re-import blocklist (R-015, R-016)."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import CreatedAt, IdPk, one_of, phone_ok

CLAIM_STATUSES = ("pending", "approved", "rejected")
REMOVAL_STATUSES = ("pending", "done", "rejected")
BLOCKLIST_KINDS = ("phone", "source_url")


class ProfileClaim(IdPk, CreatedAt, Base):
    """A "This is me" claim on an imported profile.

    A verified phone that matches `source_phone` is approved at once; a mismatch stays
    pending for manual review in the admin panel.
    """

    __tablename__ = "profile_claims"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    phone: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(one_of("status", CLAIM_STATUSES), name="status"),
        CheckConstraint(phone_ok("phone"), name="phone_format"),
        CheckConstraint("(status = 'pending') = (resolved_at IS NULL)", name="resolved_at"),
        # A user has at most one pending claim per profile.
        Index(
            "uq_profile_claims_pending",
            "master_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )


class DataRemovalRequest(IdPk, CreatedAt, Base):
    """A "Remove my data" request for an unclaimed profile, which is deleted once it is done."""

    __tablename__ = "data_removal_requests"

    master_id: Mapped[int | None] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="SET NULL"), index=True
    )
    # How to reach the person, if they left a contact.
    contact: Mapped[str | None] = mapped_column(String(254))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), server_default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(one_of("status", REMOVAL_STATUSES), name="status"),
        CheckConstraint("(status = 'pending') = (resolved_at IS NULL)", name="resolved_at"),
    )


class ImportBlocklist(IdPk, CreatedAt, Base):
    """People who asked to be removed are never re-imported (R-015).

    Stores HMAC-SHA256(server secret, normalized phone or source URL). A plain SHA-256 of a
    9-digit phone number can be reversed by trying every number; a keyed hash cannot.
    """

    __tablename__ = "import_blocklist"

    kind: Mapped[str] = mapped_column(String(10))
    value_sha256: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(one_of("kind", BLOCKLIST_KINDS), name="kind"),
        CheckConstraint("value_sha256 ~ '^[0-9a-f]{64}$'", name="value_sha256_hex"),
        UniqueConstraint("kind", "value_sha256"),
    )
