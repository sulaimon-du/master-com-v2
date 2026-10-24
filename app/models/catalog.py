from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.common import REF_SLUG_RE, IdPk, not_blank, one_of

# "lat" is for hand-added Latin spellings; Cyrillic <-> Latin transliteration itself
# happens at search time (BRIEF §2, §4.7).
SYNONYM_LANGS = ("ru", "tg", "lat")


class Category(Base):
    """A direction: the first level of the catalog (BRIEF §5)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_ru: Mapped[str] = mapped_column(String(120))
    name_tg: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())

    specialties: Mapped[list["Specialty"]] = relationship(
        back_populates="category", order_by="Specialty.sort_order"
    )

    __table_args__ = (CheckConstraint(f"slug ~ '{REF_SLUG_RE}'", name="slug_format"),)


class Specialty(Base):
    """The second level of the catalog: what a master does and what a request asks for."""

    __tablename__ = "specialties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_ru: Mapped[str] = mapped_column(String(120))
    name_tg: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())

    category: Mapped[Category] = relationship(back_populates="specialties")
    synonyms: Mapped[list["SpecialtySynonym"]] = relationship(
        back_populates="specialty", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint(f"slug ~ '{REF_SLUG_RE}'", name="slug_format"),)


class SpecialtySynonym(IdPk, Base):
    """Another word people search with for a specialty, stored lowercased."""

    __tablename__ = "specialty_synonyms"

    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(String(3))
    text: Mapped[str] = mapped_column(String(120))

    specialty: Mapped[Specialty] = relationship(back_populates="synonyms")

    __table_args__ = (
        CheckConstraint(one_of("lang", SYNONYM_LANGS), name="lang"),
        CheckConstraint("text = lower(text)", name="text_lowercase"),
        CheckConstraint(not_blank("text"), name="text_not_blank"),
        UniqueConstraint("specialty_id", "lang", "text"),
    )
