"""Request input and output models; templates never get ORM rows.

`RequestInput` only parses types. The rules (lengths, ranges, places) live in the service so
every caller gets the same error codes, whatever form or script built the input.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.master import PhotoView


class RequestInput(BaseModel):
    specialty_ids: list[int]
    city_id: int
    district_id: int | None = None
    description: str
    budget_amount: Decimal | None = None
    budget_unit: str | None = None
    timing: str
    timing_date: date | None = None
    address: str | None = None


class RequestCard(BaseModel):
    id: int
    specialties: list[str]
    place: str
    status: str
    responses_count: int
    created_at: datetime
    thumb_url: str | None


class RequestView(RequestCard):
    """The client's own view of a request. `address` is never part of any master-facing view."""

    description: str
    budget_amount: Decimal | None
    budget_unit: str | None
    timing: str
    timing_date: date | None
    address: str | None
    photos: list[PhotoView] = Field(default_factory=list)
    specialty_ids: list[int]
    city_id: int
    district_id: int | None
    close_reason: str | None
    feed_paused: bool
    editable: bool
