"""Shared columns and value lists for the models.

Status and kind fields are short strings with a named CHECK constraint instead of
Postgres ENUM types: adding a value is a one-line migration, and the names stay stable.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, func
from sqlalchemy.orm import Mapped, mapped_column

LANGS = ("ru", "tg")
# Prices in requests and responses (R-013 C3).
PRICE_UNITS = ("fixed", "m2", "running_m", "piece", "hour")
# E.164 without spaces, e.g. +992901234567.
PHONE_RE = "^[+][1-9][0-9]{6,14}$"
# Slugs of reference data: lowercase Latin words joined by single hyphens.
REF_SLUG_RE = "^[a-z0-9]+(-[a-z0-9]+)*$"


def one_of(column: str, values: Iterable[str]) -> str:
    """SQL for a CHECK constraint: `column` holds one of `values`."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def phone_ok(column: str) -> str:
    return f"{column} IS NULL OR {column} ~ '{PHONE_RE}'"


def not_blank(column: str) -> str:
    return f"btrim({column}) <> ''"


class IdPk:
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Timestamps(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
