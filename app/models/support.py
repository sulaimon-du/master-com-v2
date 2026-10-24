from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import CreatedAt, IdPk, not_blank, one_of

THREAD_STATUSES = ("open", "closed")


class SupportThread(IdPk, CreatedAt, Base):
    """An "Ask a question" thread (BRIEF §4.12a, R-013 E4).

    A signed-in user's thread lives in the site; a guest's replies go to `guest_email`.
    Closes after 7 days without messages.
    """

    __tablename__ = "support_threads"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    guest_email: Mapped[str | None] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(10), server_default="open")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("num_nonnulls(user_id, guest_email) = 1", name="user_or_guest"),
        CheckConstraint("guest_email = lower(guest_email)", name="guest_email_lowercase"),
        CheckConstraint(one_of("status", THREAD_STATUSES), name="status"),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="closed_at"),
        # The closing job scans open threads by last activity.
        Index("ix_support_threads_status_last_message_at", "status", "last_message_at"),
    )


class SupportMessage(IdPk, CreatedAt, Base):
    __tablename__ = "support_messages"

    thread_id: Mapped[int] = mapped_column(ForeignKey("support_threads.id", ondelete="CASCADE"))
    # NULL for a guest's message.
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_from_admin: Mapped[bool] = mapped_column(server_default=false())
    body: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(not_blank("body"), name="body_not_blank"),
        CheckConstraint("NOT is_from_admin OR author_id IS NOT NULL", name="admin_has_author"),
        Index("ix_support_messages_thread_created_at", "thread_id", "created_at"),
    )
