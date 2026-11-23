from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.catalog import SYNONYM_LANGS
from app.models.common import IdPk, not_blank, one_of


def _int_array() -> Mapped[list[int]]:
    return mapped_column(ARRAY(Integer), server_default=text("'{}'"))


class MasterSearch(Base):
    """Denormalized search row per master profile (D1); rebuilt by the search service.

    `document` and `names` are lowercased and transliterated to Latin for trigram matching.
    """

    __tablename__ = "master_search"

    master_id: Mapped[int] = mapped_column(
        ForeignKey("master_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    document: Mapped[str] = mapped_column(Text, server_default="")
    names: Mapped[str] = mapped_column(Text, server_default="")
    is_visible: Mapped[bool] = mapped_column(server_default=false())
    is_available: Mapped[bool] = mapped_column(server_default=false())
    has_photos: Mapped[bool] = mapped_column(server_default=false())
    rating_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    specialty_ids: Mapped[list[int]] = _int_array()
    primary_specialty_ids: Mapped[list[int]] = _int_array()
    category_ids: Mapped[list[int]] = _int_array()
    city_ids: Mapped[list[int]] = _int_array()
    district_ids: Mapped[list[int]] = _int_array()
    whole_city_ids: Mapped[list[int]] = _int_array()
    named_district_ids: Mapped[list[int]] = _int_array()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_master_search_document_trgm",
            "document",
            postgresql_using="gin",
            postgresql_ops={"document": "gin_trgm_ops"},
        ),
        Index(
            "ix_master_search_names_trgm",
            "names",
            postgresql_using="gin",
            postgresql_ops={"names": "gin_trgm_ops"},
        ),
        Index("ix_master_search_specialty_ids", "specialty_ids", postgresql_using="gin"),
        Index(
            "ix_master_search_primary_specialty_ids",
            "primary_specialty_ids",
            postgresql_using="gin",
        ),
        Index("ix_master_search_category_ids", "category_ids", postgresql_using="gin"),
        Index("ix_master_search_city_ids", "city_ids", postgresql_using="gin"),
        Index("ix_master_search_district_ids", "district_ids", postgresql_using="gin"),
        Index("ix_master_search_whole_city_ids", "whole_city_ids", postgresql_using="gin"),
        Index("ix_master_search_named_district_ids", "named_district_ids", postgresql_using="gin"),
        Index(
            "ix_master_search_rating_score_visible",
            "rating_score",
            postgresql_where=text("is_visible"),
        ),
    )


class PlaceSynonym(IdPk, Base):
    """Old or alternative name of a city or district, stored lowercased (D8)."""

    __tablename__ = "place_synonyms"

    city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="CASCADE"))
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(String(3))
    text: Mapped[str] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint("(city_id IS NULL) <> (district_id IS NULL)", name="one_place"),
        CheckConstraint(one_of("lang", SYNONYM_LANGS), name="lang"),
        CheckConstraint("text = lower(text)", name="text_lowercase"),
        CheckConstraint(not_blank("text"), name="text_not_blank"),
        Index("ix_place_synonyms_city_id", "city_id"),
        Index("ix_place_synonyms_district_id", "district_id"),
        # city and district ids are different spaces, so each side gets its own coalesce slot.
        Index(
            "uq_place_synonyms_place_lang_text",
            func.coalesce(city_id, 0),
            func.coalesce(district_id, 0),
            "lang",
            "text",
            unique=True,
        ),
    )
