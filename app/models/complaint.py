from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import CreatedAt, IdPk, not_blank, one_of

# PRODUCT_RULES C7, plus wrong_number for imported profiles (R-016).
COMPLAINT_REASONS = ("spam", "fraud", "insult", "fake_photos", "wrong_number", "other")
COMPLAINT_STATUSES = ("open", "resolved", "dismissed")
# One nullable foreign key per kind of target, exactly one set. Chat messages get a column
# when chat ships (week 10).
TARGET_COLUMNS = ("master_profile_id", "request_id", "review_id")


class Complaint(IdPk, CreatedAt, Base):
    """A "Report" from a signed-in user (BRIEF §4.11)."""

    __tablename__ = "complaints"

    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Deleting a profile ("Remove my data", R-015) deletes the complaints about it.
    master_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"), index=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"), index=True)
    reason: Mapped[str] = mapped_column(String(15))
    text: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(10), server_default="open")
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(f"num_nonnulls({', '.join(TARGET_COLUMNS)}) = 1", name="one_target"),
        CheckConstraint(one_of("reason", COMPLAINT_REASONS), name="reason"),
        CheckConstraint(one_of("status", COMPLAINT_STATUSES), name="status"),
        CheckConstraint(
            f"reason <> 'other' OR (text IS NOT NULL AND {not_blank('text')})",
            name="other_needs_text",
        ),
        CheckConstraint("(status = 'open') = (resolved_at IS NULL)", name="resolved_at"),
        Index("ix_complaints_status_created_at", "status", "created_at"),
    )
