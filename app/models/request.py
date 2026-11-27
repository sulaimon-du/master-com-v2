"""The order exchange (BRIEF §4.8).

Classes are JobRequest and JobResponse so they never shadow FastAPI's Request and Response.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import (
    PRICE_UNITS,
    CreatedAt,
    IdPk,
    Timestamps,
    not_blank,
    one_of,
    phone_ok,
)

# open -> master_chosen -> completed; closed and cancelled end it early (BRIEF §4.8).
# closed: the client (or the 30-day auto-close, PRODUCT_RULES C4) ended it; cancelled: withdrawn.
REQUEST_STATUSES = ("open", "master_chosen", "completed", "closed", "cancelled")
TIMINGS = ("urgent", "date", "flexible")
# Why a request ended: the client closed it ("no longer needed"), cancelled it ("posted by
# mistake"), or the C4/C5 timers did (PRODUCT_RULES).
CLOSE_REASONS = ("client_closed", "client_cancelled", "auto_expired", "auto_stale")
# The 15-response limit: the feed pauses after this many active responses (and again every +15).
FEED_PAUSE_STEP = 15
MAX_REQUEST_PHOTOS = 4


class JobRequest(IdPk, Timestamps, Base):
    __tablename__ = "requests"

    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    # Required only when the city has districts (R-018).
    district_id: Mapped[int | None] = mapped_column(index=True)
    description: Mapped[str] = mapped_column(Text)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_unit: Mapped[str | None] = mapped_column(String(10))
    timing: Mapped[str] = mapped_column(String(10))
    timing_date: Mapped[date | None] = mapped_column(Date)
    # Exact address: never shown in lists; sent to the chosen master on request (BRIEF §4.6).
    address: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(15), server_default="open")
    chosen_master_id: Mapped[int | None] = mapped_column(
        ForeignKey("master_profiles.id"), index=True
    )
    responses_count: Mapped[int] = mapped_column(Integer, server_default="0")
    # Set when the request leaves the feed after 15 responses; cleared when the client reopens it.
    feed_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Lifecycle clocks. C4 counts from `opened_at` (creation and every reopen), C5 from
    # `master_chosen_at`.
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    master_chosen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(20))
    expiry_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where the chosen master reaches the client; shown to that master only (spec D10.1).
    client_phone: Mapped[str | None] = mapped_column(String(16))
    # Pause when active responses reach this number; "reopen" sets it to responses_count + 15.
    feed_pause_at: Mapped[int] = mapped_column(Integer, server_default=str(FEED_PAUSE_STEP))

    __table_args__ = (
        ForeignKeyConstraint(
            ["district_id", "city_id"],
            ["districts.id", "districts.city_id"],
            name="fk_requests_district_in_city",
        ),
        CheckConstraint(one_of("status", REQUEST_STATUSES), name="status"),
        CheckConstraint(one_of("timing", TIMINGS), name="timing"),
        CheckConstraint(one_of("budget_unit", PRICE_UNITS), name="budget_unit"),
        CheckConstraint(not_blank("description"), name="description_not_blank"),
        CheckConstraint(
            "(budget_amount IS NULL) = (budget_unit IS NULL)", name="budget_amount_with_unit"
        ),
        CheckConstraint("budget_amount > 0", name="budget_positive"),
        CheckConstraint("(timing = 'date') = (timing_date IS NOT NULL)", name="timing_date"),
        CheckConstraint(
            "status NOT IN ('master_chosen', 'completed') OR chosen_master_id IS NOT NULL",
            name="chosen_master_set",
        ),
        CheckConstraint("status <> 'open' OR chosen_master_id IS NULL", name="open_has_no_master"),
        CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name="completed_at"),
        CheckConstraint(
            "(status IN ('closed', 'cancelled')) = (closed_at IS NOT NULL)", name="closed_at"
        ),
        CheckConstraint("responses_count >= 0", name="responses_count_not_negative"),
        CheckConstraint(one_of("close_reason", CLOSE_REASONS), name="close_reason_value"),
        CheckConstraint(
            "(status IN ('closed', 'cancelled')) = (close_reason IS NOT NULL)", name="close_reason"
        ),
        CheckConstraint(
            "(chosen_master_id IS NOT NULL) = (master_chosen_at IS NOT NULL)",
            name="master_chosen_at",
        ),
        CheckConstraint(phone_ok("client_phone"), name="client_phone_format"),
        CheckConstraint(
            "status NOT IN ('master_chosen', 'completed') OR client_phone IS NOT NULL",
            name="client_phone_when_chosen",
        ),
        CheckConstraint("feed_pause_at > 0", name="feed_pause_at_positive"),
        # Masters' feed: open requests, newest first.
        Index("ix_requests_status_created_at", "status", "created_at"),
        # Timers: C4 scans open requests by age, C5 scans chosen ones.
        Index("ix_requests_status_opened_at", "status", "opened_at"),
        Index("ix_requests_status_master_chosen_at", "status", "master_chosen_at"),
    )


class RequestSpecialty(Base):
    __tablename__ = "request_specialties"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )
    specialty_id: Mapped[int] = mapped_column(
        ForeignKey("specialties.id"), primary_key=True, index=True
    )


class RequestPhoto(IdPk, CreatedAt, Base):
    __tablename__ = "request_photos"

    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    thumb_key: Mapped[str] = mapped_column(String(255), unique=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (
        CheckConstraint("width > 0 AND height > 0", name="size_positive"),
        # Positions 0..3 and unique per request: the database itself caps a request at 4 photos.
        CheckConstraint(f"position BETWEEN 0 AND {MAX_REQUEST_PHOTOS - 1}", name="position"),
        UniqueConstraint("request_id", "position"),
    )


class JobResponse(IdPk, Timestamps, Base):
    """A master's "Ready to take it". One per master per request (BRIEF §4.8)."""

    __tablename__ = "responses"

    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    master_id: Mapped[int] = mapped_column(ForeignKey("master_profiles.id"), index=True)
    message: Mapped[str | None] = mapped_column(String(500))
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_unit: Mapped[str | None] = mapped_column(String(10))
    # Soft withdraw; responding again clears it (spec D10.2).
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("request_id", "master_id"),
        CheckConstraint(one_of("price_unit", PRICE_UNITS), name="price_unit"),
        CheckConstraint(
            "(price_amount IS NULL) = (price_unit IS NULL)", name="price_amount_with_unit"
        ),
        CheckConstraint("price_amount > 0", name="price_positive"),
    )
