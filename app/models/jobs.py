"""Background jobs and notification preferences (weeks 8-9, spec D6 and D8)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import IdPk, Timestamps, not_blank, one_of

# What a user can switch off, per channel (spec D10.3). All default to on.
EVENTS = (
    "new_request",
    "new_response",
    "chosen",
    "request_cancelled",
    "review_prompt",
    "expiry_reminder",
    "done_check",
)
CHANNELS = ("telegram", "email")


class Job(IdPk, Timestamps, Base):
    """A unit of background work. Enqueued in the same transaction as the change that needs it.

    `payload` holds ids only, never personal data. `last_error` holds the exception type only.
    """

    __tablename__ = "jobs"

    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    # Unique when set, so enqueuing the same thing twice is a no-op.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)

    __table_args__ = (
        CheckConstraint(not_blank("kind"), name="kind_not_blank"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        # The worker's claim query only looks at jobs still waiting.
        Index(
            "ix_jobs_run_at_pending",
            "run_at",
            postgresql_where=text("done_at IS NULL AND failed_at IS NULL"),
        ),
    )


class NotificationPref(Base):
    """One opt-out switch. A missing row means enabled."""

    __tablename__ = "notification_prefs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    event: Mapped[str] = mapped_column(String(24), primary_key=True)
    channel: Mapped[str] = mapped_column(String(10), primary_key=True)
    enabled: Mapped[bool]

    __table_args__ = (
        CheckConstraint(one_of("event", EVENTS), name="event"),
        CheckConstraint(one_of("channel", CHANNELS), name="channel"),
    )
