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
from app.models.common import REF_SLUG_RE, one_of

REGIONS = ("dushanbe", "rrs", "sughd", "khatlon", "gbao")
# A top-level place is a city (шаҳр) or a rural district (ноҳия); see R-018.
CITY_KINDS = ("city", "rural_district")


class City(Base):
    """Top-level place a master works in or a request is for. Seeded, editable in the admin."""

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_ru: Mapped[str] = mapped_column(String(120))
    name_tg: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(15))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())

    districts: Mapped[list["District"]] = relationship(
        back_populates="city", order_by="District.sort_order"
    )

    __table_args__ = (
        CheckConstraint(f"slug ~ '{REF_SLUG_RE}'", name="slug_format"),
        CheckConstraint(one_of("region", REGIONS), name="region"),
        CheckConstraint(one_of("kind", CITY_KINDS), name="kind"),
    )


class District(Base):
    """A part of a city, e.g. Sino in Dushanbe."""

    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    slug: Mapped[str] = mapped_column(String(60))
    name_ru: Mapped[str] = mapped_column(String(120))
    name_tg: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_active: Mapped[bool] = mapped_column(server_default=true())

    city: Mapped[City] = relationship(back_populates="districts")

    __table_args__ = (
        CheckConstraint(f"slug ~ '{REF_SLUG_RE}'", name="slug_format"),
        UniqueConstraint("city_id", "slug"),
        # Target of composite foreign keys (district_id, city_id): a row that names both a
        # city and a district can only name a district of that city.
        UniqueConstraint("id", "city_id"),
    )
