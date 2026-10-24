from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.common import IdPk, Timestamps, not_blank


class Review(IdPk, Timestamps, Base):
    """One review per completed request, by its client (BRIEF §4.11, PRODUCT_RULES C6)."""

    __tablename__ = "reviews"

    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), unique=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("master_profiles.id"), index=True)
    # Points to a user whose personal data is wiped on deletion: the review stays anonymized.
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text)
    # The master can reply once, publicly.
    master_reply: Mapped[str | None] = mapped_column(Text)
    master_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint(not_blank("text"), name="text_not_blank"),
        CheckConstraint(
            "(master_reply IS NULL) = (master_replied_at IS NULL)", name="reply_with_time"
        ),
    )
