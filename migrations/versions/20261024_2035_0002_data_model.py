"""data model: users, masters, catalog, places, requests, portfolio, moderation, support

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 20:35:53.523598
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name_ru", sa.String(length=120), nullable=False),
        sa.Column("name_tg", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_categories_slug_format")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("slug", name=op.f("uq_categories_slug")),
    )
    op.create_table(
        "cities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name_ru", sa.String(length=120), nullable=False),
        sa.Column("name_tg", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("kind IN ('city', 'rural_district')", name=op.f("ck_cities_kind")),
        sa.CheckConstraint(
            "region IN ('dushanbe', 'rrs', 'sughd', 'khatlon', 'gbao')",
            name=op.f("ck_cities_region"),
        ),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_cities_slug_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cities")),
        sa.UniqueConstraint("slug", name=op.f("uq_cities_slug")),
    )
    op.create_table(
        "import_blocklist",
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('phone', 'source_url')", name=op.f("ck_import_blocklist_kind")
        ),
        sa.CheckConstraint(
            "value_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_import_blocklist_value_sha256_hex")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_blocklist")),
        sa.UniqueConstraint(
            "kind", "value_sha256", name=op.f("uq_import_blocklist_kind_value_sha256")
        ),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("google_sub", sa.String(length=255), nullable=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locale", sa.String(length=2), server_default="ru", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("locale IN ('ru', 'tg')", name=op.f("ck_users_locale")),
        sa.CheckConstraint(
            "phone IS NULL OR phone ~ '^[+][1-9][0-9]{6,14}$'", name=op.f("ck_users_phone_format")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint(
            "phone_verified_at IS NULL OR phone IS NOT NULL",
            name=op.f("ck_users_verified_phone_present"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("google_sub", name=op.f("uq_users_google_sub")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_users_telegram_id")),
    )
    op.create_index(
        "uq_users_verified_phone",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("phone_verified_at IS NOT NULL"),
    )
    op.create_table(
        "districts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("city_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name_ru", sa.String(length=120), nullable=False),
        sa.Column("name_tg", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_districts_slug_format")
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_districts_city_id_cities")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_districts")),
        sa.UniqueConstraint("city_id", "slug", name=op.f("uq_districts_city_id_slug")),
        sa.UniqueConstraint("id", "city_id", name=op.f("uq_districts_id_city_id")),
    )
    op.create_table(
        "master_profiles",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("avatar_key", sa.String(length=255), nullable=True),
        sa.Column("headline", sa.String(length=80), nullable=True),
        sa.Column("bio", sa.String(length=1000), nullable=True),
        sa.Column("experience_years", sa.SmallInteger(), nullable=True),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("plan", sa.String(length=20), server_default="free", nullable=False),
        sa.Column("contact_phone", sa.String(length=16), nullable=True),
        sa.Column("contact_telegram", sa.String(length=32), nullable=True),
        sa.Column("contact_whatsapp", sa.String(length=16), nullable=True),
        sa.Column("contact_instagram", sa.String(length=30), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_requests_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reviews_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rating_avg", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("rating_score", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("origin", sa.String(length=10), server_default="self", nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_phone", sa.String(length=16), nullable=True),
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "contact_phone IS NULL OR contact_phone ~ '^[+][1-9][0-9]{6,14}$'",
            name=op.f("ck_master_profiles_contact_phone_format"),
        ),
        sa.CheckConstraint(
            "contact_whatsapp IS NULL OR contact_whatsapp ~ '^[+][1-9][0-9]{6,14}$'",
            name=op.f("ck_master_profiles_contact_whatsapp_format"),
        ),
        sa.CheckConstraint(
            "origin <> 'admin' OR consent_at IS NOT NULL",
            name=op.f("ck_master_profiles_admin_has_consent"),
        ),
        sa.CheckConstraint(
            "origin <> 'import' OR (source_url IS NOT NULL AND imported_at IS NOT NULL)",
            name=op.f("ck_master_profiles_import_has_source"),
        ),
        sa.CheckConstraint(
            "origin <> 'self' OR user_id IS NOT NULL", name=op.f("ck_master_profiles_self_has_user")
        ),
        sa.CheckConstraint(
            "origin IN ('self', 'admin', 'import')", name=op.f("ck_master_profiles_origin")
        ),
        sa.CheckConstraint("plan IN ('free')", name=op.f("ck_master_profiles_plan")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9-]{3,40}$'", name=op.f("ck_master_profiles_slug_format")
        ),
        sa.CheckConstraint(
            "source_phone IS NULL OR source_phone ~ '^[+][1-9][0-9]{6,14}$'",
            name=op.f("ck_master_profiles_source_phone_format"),
        ),
        sa.CheckConstraint(
            "claimed_at IS NULL OR user_id IS NOT NULL",
            name=op.f("ck_master_profiles_claimed_has_user"),
        ),
        sa.CheckConstraint(
            "completed_requests_count >= 0 AND reviews_count >= 0 AND call_count >= 0",
            name=op.f("ck_master_profiles_counters_not_negative"),
        ),
        sa.CheckConstraint(
            "experience_years BETWEEN 0 AND 60", name=op.f("ck_master_profiles_experience_years")
        ),
        sa.CheckConstraint(
            "rating_avg BETWEEN 1 AND 5", name=op.f("ck_master_profiles_rating_avg_range")
        ),
        sa.CheckConstraint(
            "rating_score BETWEEN 1 AND 5", name=op.f("ck_master_profiles_rating_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_master_profiles_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_profiles")),
        sa.UniqueConstraint("slug", name=op.f("uq_master_profiles_slug")),
        sa.UniqueConstraint("user_id", name=op.f("uq_master_profiles_user_id")),
    )
    op.create_table(
        "specialties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name_ru", sa.String(length=120), nullable=False),
        sa.Column("name_tg", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_specialties_slug_format")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], name=op.f("fk_specialties_category_id_categories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_specialties")),
        sa.UniqueConstraint("slug", name=op.f("uq_specialties_slug")),
    )
    op.create_index(
        op.f("ix_specialties_category_id"), "specialties", ["category_id"], unique=False
    )
    op.create_table(
        "support_threads",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("guest_email", sa.String(length=254), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)",
            name=op.f("ck_support_threads_closed_at"),
        ),
        sa.CheckConstraint("status IN ('open', 'closed')", name=op.f("ck_support_threads_status")),
        sa.CheckConstraint(
            "guest_email = lower(guest_email)",
            name=op.f("ck_support_threads_guest_email_lowercase"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(user_id, guest_email) = 1", name=op.f("ck_support_threads_user_or_guest")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_support_threads_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_threads")),
    )
    op.create_index(
        "ix_support_threads_status_last_message_at",
        "support_threads",
        ["status", "last_message_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_support_threads_user_id"), "support_threads", ["user_id"], unique=False
    )
    op.create_table(
        "data_removal_requests",
        sa.Column("master_id", sa.BigInteger(), nullable=True),
        sa.Column("contact", sa.String(length=254), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name=op.f("ck_data_removal_requests_resolved_at"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'rejected')",
            name=op.f("ck_data_removal_requests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_data_removal_requests_master_id_master_profiles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_removal_requests")),
    )
    op.create_index(
        op.f("ix_data_removal_requests_master_id"),
        "data_removal_requests",
        ["master_id"],
        unique=False,
    )
    op.create_table(
        "master_areas",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.Integer(), nullable=False),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_master_areas_city_id_cities")
        ),
        sa.ForeignKeyConstraint(
            ["district_id", "city_id"],
            ["districts.id", "districts.city_id"],
            name="fk_master_areas_district_in_city",
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_master_areas_master_id_master_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_areas")),
    )
    op.create_index(op.f("ix_master_areas_city_id"), "master_areas", ["city_id"], unique=False)
    op.create_index(
        op.f("ix_master_areas_district_id"), "master_areas", ["district_id"], unique=False
    )
    op.create_index(op.f("ix_master_areas_master_id"), "master_areas", ["master_id"], unique=False)
    op.create_index(
        "uq_master_areas_district",
        "master_areas",
        ["master_id", "district_id"],
        unique=True,
        postgresql_where=sa.text("district_id IS NOT NULL"),
    )
    op.create_index(
        "uq_master_areas_whole_city",
        "master_areas",
        ["master_id", "city_id"],
        unique=True,
        postgresql_where=sa.text("district_id IS NULL"),
    )
    op.create_table(
        "master_specialties",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("specialty_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_master_specialties_master_id_master_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["specialty_id"],
            ["specialties.id"],
            name=op.f("fk_master_specialties_specialty_id_specialties"),
        ),
        sa.PrimaryKeyConstraint("master_id", "specialty_id", name=op.f("pk_master_specialties")),
    )
    op.create_index(
        op.f("ix_master_specialties_specialty_id"),
        "master_specialties",
        ["specialty_id"],
        unique=False,
    )
    op.create_table(
        "portfolio_collections",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(title) <> ''", name=op.f("ck_portfolio_collections_title_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_portfolio_collections_master_id_master_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_collections")),
    )
    op.create_index(
        "ix_portfolio_collections_master_position",
        "portfolio_collections",
        ["master_id", "position"],
        unique=False,
    )
    op.create_table(
        "profile_claims",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("resolved_by_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name=op.f("ck_profile_claims_resolved_at"),
        ),
        sa.CheckConstraint(
            "phone IS NULL OR phone ~ '^[+][1-9][0-9]{6,14}$'",
            name=op.f("ck_profile_claims_phone_format"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name=op.f("ck_profile_claims_status")
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_profile_claims_master_id_master_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"], ["users.id"], name=op.f("fk_profile_claims_resolved_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profile_claims_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_claims")),
    )
    op.create_index(
        op.f("ix_profile_claims_master_id"), "profile_claims", ["master_id"], unique=False
    )
    op.create_index(op.f("ix_profile_claims_user_id"), "profile_claims", ["user_id"], unique=False)
    op.create_index(
        "uq_profile_claims_pending",
        "profile_claims",
        ["master_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "requests",
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("city_id", sa.Integer(), nullable=False),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("budget_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("budget_unit", sa.String(length=10), nullable=True),
        sa.Column("timing", sa.String(length=10), nullable=False),
        sa.Column("timing_date", sa.Date(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=15), server_default="open", nullable=False),
        sa.Column("chosen_master_id", sa.BigInteger(), nullable=True),
        sa.Column("responses_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("feed_paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name=op.f("ck_requests_completed_at"),
        ),
        sa.CheckConstraint(
            "(status IN ('closed', 'cancelled')) = (closed_at IS NOT NULL)",
            name=op.f("ck_requests_closed_at"),
        ),
        sa.CheckConstraint(
            "(timing = 'date') = (timing_date IS NOT NULL)", name=op.f("ck_requests_timing_date")
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''", name=op.f("ck_requests_description_not_blank")
        ),
        sa.CheckConstraint(
            "budget_unit IN ('fixed', 'm2', 'running_m', 'piece', 'hour')",
            name=op.f("ck_requests_budget_unit"),
        ),
        sa.CheckConstraint(
            "status <> 'open' OR chosen_master_id IS NULL",
            name=op.f("ck_requests_open_has_no_master"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'master_chosen', 'completed', 'closed', 'cancelled')",
            name=op.f("ck_requests_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('master_chosen', 'completed') OR chosen_master_id IS NOT NULL",
            name=op.f("ck_requests_chosen_master_set"),
        ),
        sa.CheckConstraint(
            "timing IN ('urgent', 'date', 'flexible')", name=op.f("ck_requests_timing")
        ),
        sa.CheckConstraint(
            "(budget_amount IS NULL) = (budget_unit IS NULL)",
            name=op.f("ck_requests_budget_amount_with_unit"),
        ),
        sa.CheckConstraint("budget_amount > 0", name=op.f("ck_requests_budget_positive")),
        sa.CheckConstraint(
            "responses_count >= 0", name=op.f("ck_requests_responses_count_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["chosen_master_id"],
            ["master_profiles.id"],
            name=op.f("fk_requests_chosen_master_id_master_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_requests_city_id_cities")
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["users.id"], name=op.f("fk_requests_client_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["district_id", "city_id"],
            ["districts.id", "districts.city_id"],
            name="fk_requests_district_in_city",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requests")),
    )
    op.create_index(
        op.f("ix_requests_chosen_master_id"), "requests", ["chosen_master_id"], unique=False
    )
    op.create_index(op.f("ix_requests_city_id"), "requests", ["city_id"], unique=False)
    op.create_index(op.f("ix_requests_client_id"), "requests", ["client_id"], unique=False)
    op.create_index(op.f("ix_requests_district_id"), "requests", ["district_id"], unique=False)
    op.create_index(
        "ix_requests_status_created_at", "requests", ["status", "created_at"], unique=False
    )
    op.create_table(
        "specialty_synonyms",
        sa.Column("specialty_id", sa.Integer(), nullable=False),
        sa.Column("lang", sa.String(length=3), nullable=False),
        sa.Column("text", sa.String(length=120), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("btrim(text) <> ''", name=op.f("ck_specialty_synonyms_text_not_blank")),
        sa.CheckConstraint("lang IN ('ru', 'tg', 'lat')", name=op.f("ck_specialty_synonyms_lang")),
        sa.CheckConstraint("text = lower(text)", name=op.f("ck_specialty_synonyms_text_lowercase")),
        sa.ForeignKeyConstraint(
            ["specialty_id"],
            ["specialties.id"],
            name=op.f("fk_specialty_synonyms_specialty_id_specialties"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_specialty_synonyms")),
        sa.UniqueConstraint(
            "specialty_id",
            "lang",
            "text",
            name=op.f("uq_specialty_synonyms_specialty_id_lang_text"),
        ),
    )
    op.create_table(
        "support_messages",
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("is_from_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(body) <> ''", name=op.f("ck_support_messages_body_not_blank")),
        sa.CheckConstraint(
            "NOT is_from_admin OR author_id IS NOT NULL",
            name=op.f("ck_support_messages_admin_has_author"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_support_messages_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["support_threads.id"],
            name=op.f("fk_support_messages_thread_id_support_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_messages")),
    )
    op.create_index(
        "ix_support_messages_thread_created_at",
        "support_messages",
        ["thread_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "portfolio_photos",
        sa.Column("collection_id", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("thumb_key", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0", name=op.f("ck_portfolio_photos_size_positive")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["portfolio_collections.id"],
            name=op.f("fk_portfolio_photos_collection_id_portfolio_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_photos")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_portfolio_photos_storage_key")),
        sa.UniqueConstraint("thumb_key", name=op.f("uq_portfolio_photos_thumb_key")),
    )
    op.create_index(
        "ix_portfolio_photos_collection_position",
        "portfolio_photos",
        ["collection_id", "position"],
        unique=False,
    )
    op.create_table(
        "request_photos",
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("thumb_key", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position BETWEEN 0 AND 3", name=op.f("ck_request_photos_position")),
        sa.CheckConstraint(
            "width > 0 AND height > 0", name=op.f("ck_request_photos_size_positive")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_request_photos_request_id_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_photos")),
        sa.UniqueConstraint(
            "request_id", "position", name=op.f("uq_request_photos_request_id_position")
        ),
        sa.UniqueConstraint("storage_key", name=op.f("uq_request_photos_storage_key")),
        sa.UniqueConstraint("thumb_key", name=op.f("uq_request_photos_thumb_key")),
    )
    op.create_table(
        "request_specialties",
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("specialty_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_request_specialties_request_id_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["specialty_id"],
            ["specialties.id"],
            name=op.f("fk_request_specialties_specialty_id_specialties"),
        ),
        sa.PrimaryKeyConstraint("request_id", "specialty_id", name=op.f("pk_request_specialties")),
    )
    op.create_index(
        op.f("ix_request_specialties_specialty_id"),
        "request_specialties",
        ["specialty_id"],
        unique=False,
    )
    op.create_table(
        "responses",
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("price_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("price_unit", sa.String(length=10), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_unit IN ('fixed', 'm2', 'running_m', 'piece', 'hour')",
            name=op.f("ck_responses_price_unit"),
        ),
        sa.CheckConstraint(
            "(price_amount IS NULL) = (price_unit IS NULL)",
            name=op.f("ck_responses_price_amount_with_unit"),
        ),
        sa.CheckConstraint("price_amount > 0", name=op.f("ck_responses_price_positive")),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_responses_master_id_master_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_responses_request_id_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_responses")),
        sa.UniqueConstraint(
            "request_id", "master_id", name=op.f("uq_responses_request_id_master_id")
        ),
    )
    op.create_index(op.f("ix_responses_master_id"), "responses", ["master_id"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("master_reply", sa.Text(), nullable=True),
        sa.Column("master_replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(text) <> ''", name=op.f("ck_reviews_text_not_blank")),
        sa.CheckConstraint(
            "(master_reply IS NULL) = (master_replied_at IS NULL)",
            name=op.f("ck_reviews_reply_with_time"),
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_reviews_rating_range")),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_reviews_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["master_id"], ["master_profiles.id"], name=op.f("fk_reviews_master_id_master_profiles")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["requests.id"], name=op.f("fk_reviews_request_id_requests")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("request_id", name=op.f("uq_reviews_request_id")),
    )
    op.create_index(op.f("ix_reviews_author_id"), "reviews", ["author_id"], unique=False)
    op.create_index(op.f("ix_reviews_master_id"), "reviews", ["master_id"], unique=False)
    op.create_table(
        "complaints",
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("master_profile_id", sa.BigInteger(), nullable=True),
        sa.Column("request_id", sa.BigInteger(), nullable=True),
        sa.Column("review_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=15), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("resolved_by_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'open') = (resolved_at IS NULL)", name=op.f("ck_complaints_resolved_at")
        ),
        sa.CheckConstraint(
            "reason <> 'other' OR (text IS NOT NULL AND btrim(text) <> '')",
            name=op.f("ck_complaints_other_needs_text"),
        ),
        sa.CheckConstraint(
            "reason IN ('spam', 'fraud', 'insult', 'fake_photos', 'wrong_number', 'other')",
            name=op.f("ck_complaints_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')", name=op.f("ck_complaints_status")
        ),
        sa.CheckConstraint(
            "num_nonnulls(master_profile_id, request_id, review_id) = 1",
            name=op.f("ck_complaints_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["master_profile_id"],
            ["master_profiles.id"],
            name=op.f("fk_complaints_master_profile_id_master_profiles"),
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"], ["users.id"], name=op.f("fk_complaints_reporter_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["requests.id"], name=op.f("fk_complaints_request_id_requests")
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"], ["users.id"], name=op.f("fk_complaints_resolved_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["review_id"], ["reviews.id"], name=op.f("fk_complaints_review_id_reviews")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_complaints")),
    )
    op.create_index(
        op.f("ix_complaints_master_profile_id"), "complaints", ["master_profile_id"], unique=False
    )
    op.create_index(op.f("ix_complaints_reporter_id"), "complaints", ["reporter_id"], unique=False)
    op.create_index(op.f("ix_complaints_request_id"), "complaints", ["request_id"], unique=False)
    op.create_index(op.f("ix_complaints_review_id"), "complaints", ["review_id"], unique=False)
    op.create_index(
        "ix_complaints_status_created_at", "complaints", ["status", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_complaints_status_created_at", table_name="complaints")
    op.drop_index(op.f("ix_complaints_review_id"), table_name="complaints")
    op.drop_index(op.f("ix_complaints_request_id"), table_name="complaints")
    op.drop_index(op.f("ix_complaints_reporter_id"), table_name="complaints")
    op.drop_index(op.f("ix_complaints_master_profile_id"), table_name="complaints")
    op.drop_table("complaints")
    op.drop_index(op.f("ix_reviews_master_id"), table_name="reviews")
    op.drop_index(op.f("ix_reviews_author_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_responses_master_id"), table_name="responses")
    op.drop_table("responses")
    op.drop_index(op.f("ix_request_specialties_specialty_id"), table_name="request_specialties")
    op.drop_table("request_specialties")
    op.drop_table("request_photos")
    op.drop_index("ix_portfolio_photos_collection_position", table_name="portfolio_photos")
    op.drop_table("portfolio_photos")
    op.drop_index("ix_support_messages_thread_created_at", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_table("specialty_synonyms")
    op.drop_index("ix_requests_status_created_at", table_name="requests")
    op.drop_index(op.f("ix_requests_district_id"), table_name="requests")
    op.drop_index(op.f("ix_requests_client_id"), table_name="requests")
    op.drop_index(op.f("ix_requests_city_id"), table_name="requests")
    op.drop_index(op.f("ix_requests_chosen_master_id"), table_name="requests")
    op.drop_table("requests")
    op.drop_index(
        "uq_profile_claims_pending",
        table_name="profile_claims",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_profile_claims_user_id"), table_name="profile_claims")
    op.drop_index(op.f("ix_profile_claims_master_id"), table_name="profile_claims")
    op.drop_table("profile_claims")
    op.drop_index("ix_portfolio_collections_master_position", table_name="portfolio_collections")
    op.drop_table("portfolio_collections")
    op.drop_index(op.f("ix_master_specialties_specialty_id"), table_name="master_specialties")
    op.drop_table("master_specialties")
    op.drop_index(
        "uq_master_areas_whole_city",
        table_name="master_areas",
        postgresql_where=sa.text("district_id IS NULL"),
    )
    op.drop_index(
        "uq_master_areas_district",
        table_name="master_areas",
        postgresql_where=sa.text("district_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_master_areas_master_id"), table_name="master_areas")
    op.drop_index(op.f("ix_master_areas_district_id"), table_name="master_areas")
    op.drop_index(op.f("ix_master_areas_city_id"), table_name="master_areas")
    op.drop_table("master_areas")
    op.drop_index(op.f("ix_data_removal_requests_master_id"), table_name="data_removal_requests")
    op.drop_table("data_removal_requests")
    op.drop_index(op.f("ix_support_threads_user_id"), table_name="support_threads")
    op.drop_index("ix_support_threads_status_last_message_at", table_name="support_threads")
    op.drop_table("support_threads")
    op.drop_index(op.f("ix_specialties_category_id"), table_name="specialties")
    op.drop_table("specialties")
    op.drop_table("master_profiles")
    op.drop_table("districts")
    op.drop_index(
        "uq_users_verified_phone",
        table_name="users",
        postgresql_where=sa.text("phone_verified_at IS NOT NULL"),
    )
    op.drop_table("users")
    op.drop_table("import_blocklist")
    op.drop_table("cities")
    op.drop_table("categories")
