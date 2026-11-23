"""search: pg_trgm, master_search index table and place_synonyms

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23 22:20:42.081764
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Needed by the gin_trgm_ops indexes below. Not dropped on downgrade: other
    # objects may use it and CREATE EXTENSION IF NOT EXISTS is idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "master_search",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("document", sa.Text(), server_default="", nullable=False),
        sa.Column("names", sa.Text(), server_default="", nullable=False),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("has_photos", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rating_score", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "specialty_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "primary_specialty_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "category_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "city_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "district_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "whole_city_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "named_district_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["master_profiles.id"],
            name=op.f("fk_master_search_master_id_master_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("master_id", name=op.f("pk_master_search")),
    )
    op.create_index(
        "ix_master_search_category_ids",
        "master_search",
        ["category_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_city_ids",
        "master_search",
        ["city_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_district_ids",
        "master_search",
        ["district_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_document_trgm",
        "master_search",
        ["document"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"document": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_master_search_named_district_ids",
        "master_search",
        ["named_district_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_names_trgm",
        "master_search",
        ["names"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"names": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_master_search_primary_specialty_ids",
        "master_search",
        ["primary_specialty_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_rating_score_visible",
        "master_search",
        ["rating_score"],
        unique=False,
        postgresql_where=sa.text("is_visible"),
    )
    op.create_index(
        "ix_master_search_specialty_ids",
        "master_search",
        ["specialty_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_master_search_whole_city_ids",
        "master_search",
        ["whole_city_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "place_synonyms",
        sa.Column("city_id", sa.Integer(), nullable=True),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("lang", sa.String(length=3), nullable=False),
        sa.Column("text", sa.String(length=120), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("btrim(text) <> ''", name=op.f("ck_place_synonyms_text_not_blank")),
        sa.CheckConstraint("lang IN ('ru', 'tg', 'lat')", name=op.f("ck_place_synonyms_lang")),
        sa.CheckConstraint(
            "(city_id IS NULL) <> (district_id IS NULL)", name=op.f("ck_place_synonyms_one_place")
        ),
        sa.CheckConstraint("text = lower(text)", name=op.f("ck_place_synonyms_text_lowercase")),
        sa.ForeignKeyConstraint(
            ["city_id"],
            ["cities.id"],
            name=op.f("fk_place_synonyms_city_id_cities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["district_id"],
            ["districts.id"],
            name=op.f("fk_place_synonyms_district_id_districts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_place_synonyms")),
    )
    op.create_index("ix_place_synonyms_city_id", "place_synonyms", ["city_id"], unique=False)
    op.create_index(
        "ix_place_synonyms_district_id", "place_synonyms", ["district_id"], unique=False
    )
    op.create_index(
        "uq_place_synonyms_place_lang_text",
        "place_synonyms",
        [
            sa.literal_column("coalesce(city_id, 0)"),
            sa.literal_column("coalesce(district_id, 0)"),
            "lang",
            "text",
        ],
        unique=True,
    )


def downgrade() -> None:
    # The pg_trgm extension stays.
    op.drop_index("uq_place_synonyms_place_lang_text", table_name="place_synonyms")
    op.drop_index("ix_place_synonyms_district_id", table_name="place_synonyms")
    op.drop_index("ix_place_synonyms_city_id", table_name="place_synonyms")
    op.drop_table("place_synonyms")
    op.drop_index(
        "ix_master_search_whole_city_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_index(
        "ix_master_search_specialty_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_index(
        "ix_master_search_rating_score_visible",
        table_name="master_search",
        postgresql_where=sa.text("is_visible"),
    )
    op.drop_index(
        "ix_master_search_primary_specialty_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_index(
        "ix_master_search_names_trgm",
        table_name="master_search",
        postgresql_using="gin",
        postgresql_ops={"names": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_master_search_named_district_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_index(
        "ix_master_search_document_trgm",
        table_name="master_search",
        postgresql_using="gin",
        postgresql_ops={"document": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_master_search_district_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_index("ix_master_search_city_ids", table_name="master_search", postgresql_using="gin")
    op.drop_index(
        "ix_master_search_category_ids", table_name="master_search", postgresql_using="gin"
    )
    op.drop_table("master_search")
