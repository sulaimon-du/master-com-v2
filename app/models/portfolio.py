from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import CreatedAt, IdPk, Timestamps, not_blank

# Collection and photo limits per plan come from config (BRIEF §4.5, §4.13).


class PortfolioCollection(IdPk, Timestamps, Base):
    __tablename__ = "portfolio_collections"

    master_id: Mapped[int] = mapped_column(ForeignKey("master_profiles.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(1000))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(not_blank("title"), name="title_not_blank"),
        Index("ix_portfolio_collections_master_position", "master_id", "position"),
    )


class PortfolioPhoto(IdPk, CreatedAt, Base):
    """A processed WebP in R2 (BRIEF §4.5). Originals are never stored."""

    __tablename__ = "portfolio_photos"

    collection_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_collections.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    thumb_key: Mapped[str] = mapped_column(String(255), unique=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    # Soft delete, so "delete" can be undone; a job purges old ones from R2.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("width > 0 AND height > 0", name="size_positive"),
        Index("ix_portfolio_photos_collection_position", "collection_id", "position"),
    )
