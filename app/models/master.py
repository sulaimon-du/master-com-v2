from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import IdPk, Timestamps, one_of, phone_ok

# Plan limits live in config, not here (BRIEF §4.13); only the plan names are fixed.
PLANS = ("free",)
# self: created by the master; admin: added by the owner with recorded consent (R-009);
# import: scraped from a public source (R-015).
ORIGINS = ("self", "admin", "import")
SLUG_RE = "^[a-z0-9-]{3,40}$"


class MasterProfile(IdPk, Timestamps, Base):
    """The public master profile (BRIEF §4.4).

    Linked to a user, not part of it, so companies and crews can be added in v2 (BRIEF §3).
    An imported profile has no user until it is claimed (R-015).
    """

    __tablename__ = "master_profiles"

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    headline: Mapped[str | None] = mapped_column(String(80))
    bio: Mapped[str | None] = mapped_column(String(1000))
    experience_years: Mapped[int | None] = mapped_column(SmallInteger)
    is_available: Mapped[bool] = mapped_column(server_default=true())
    plan: Mapped[str] = mapped_column(String(20), server_default="free")

    # Contacts: rendered to signed-in users only (BRIEF §4.6).
    contact_phone: Mapped[str | None] = mapped_column(String(16))
    contact_telegram: Mapped[str | None] = mapped_column(String(32))
    contact_whatsapp: Mapped[str | None] = mapped_column(String(16))
    contact_instagram: Mapped[str | None] = mapped_column(String(30))

    # Set when onboarding finishes, or when the owner approves an imported record.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hidden by moderation, or after a "wrong number" report on an unclaimed profile (R-016).
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Trust signals, recalculated by a background job (BRIEF §4.11, docs/DATA_MODEL.md).
    completed_requests_count: Mapped[int] = mapped_column(Integer, server_default="0")
    reviews_count: Mapped[int] = mapped_column(Integer, server_default="0")
    rating_avg: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    rating_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    origin: Mapped[str] = mapped_column(String(10), server_default="self")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Imported profiles (R-015, R-016).
    source_url: Mapped[str | None] = mapped_column(String(2048))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_phone: Mapped[str | None] = mapped_column(String(16))
    call_count: Mapped[int] = mapped_column(Integer, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_RE}'", name="slug_format"),
        CheckConstraint("experience_years BETWEEN 0 AND 60", name="experience_years"),
        CheckConstraint(one_of("plan", PLANS), name="plan"),
        CheckConstraint(one_of("origin", ORIGINS), name="origin"),
        CheckConstraint(phone_ok("contact_phone"), name="contact_phone_format"),
        CheckConstraint(phone_ok("contact_whatsapp"), name="contact_whatsapp_format"),
        CheckConstraint(phone_ok("source_phone"), name="source_phone_format"),
        CheckConstraint(
            "completed_requests_count >= 0 AND reviews_count >= 0 AND call_count >= 0",
            name="counters_not_negative",
        ),
        CheckConstraint("rating_avg BETWEEN 1 AND 5", name="rating_avg_range"),
        CheckConstraint("rating_score BETWEEN 1 AND 5", name="rating_score_range"),
        CheckConstraint("origin <> 'self' OR user_id IS NOT NULL", name="self_has_user"),
        CheckConstraint("origin <> 'admin' OR consent_at IS NOT NULL", name="admin_has_consent"),
        CheckConstraint(
            "origin <> 'import' OR (source_url IS NOT NULL AND imported_at IS NOT NULL)",
            name="import_has_source",
        ),
        CheckConstraint("claimed_at IS NULL OR user_id IS NOT NULL", name="claimed_has_user"),
    )


class MasterSpecialty(Base):
    """Up to 10 specialties per master, up to 3 primary (BRIEF §4.4; enforced in the service)."""

    __tablename__ = "master_specialties"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    specialty_id: Mapped[int] = mapped_column(
        ForeignKey("specialties.id"), primary_key=True, index=True
    )
    is_primary: Mapped[bool] = mapped_column(server_default=false())


class MasterArea(IdPk, Base):
    """Where a master works: a whole city (district_id is NULL) or one of its districts."""

    __tablename__ = "master_areas"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="CASCADE"), index=True
    )
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), index=True)
    district_id: Mapped[int | None] = mapped_column(index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["district_id", "city_id"],
            ["districts.id", "districts.city_id"],
            name="fk_master_areas_district_in_city",
        ),
        Index(
            "uq_master_areas_whole_city",
            "master_id",
            "city_id",
            unique=True,
            postgresql_where=text("district_id IS NULL"),
        ),
        Index(
            "uq_master_areas_district",
            "master_id",
            "district_id",
            unique=True,
            postgresql_where=text("district_id IS NOT NULL"),
        ),
    )
