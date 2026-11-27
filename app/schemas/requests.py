"""Request input and output models; templates never get ORM rows.

`RequestInput` only parses types. The rules (lengths, ranges, places) live in the service so
every caller gets the same error codes, whatever form or script built the input.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.master import ContactsView, PhotoView


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


class IdChoice(BaseModel):
    id: int
    name: str


class SpecialtyGroup(BaseModel):
    name: str
    specialties: list[IdChoice]


class DistrictGroup(BaseModel):
    city: str
    districts: list[IdChoice]


class RequestFormOptions(BaseModel):
    """What the request form offers: directions with specialties, cities, districts by city."""

    categories: list[SpecialtyGroup]
    cities: list[IdChoice]
    district_groups: list[DistrictGroup]


class FormValues(BaseModel):
    """The form as typed (strings), so an invalid submission can be shown again unchanged."""

    specialty: list[str] = Field(default_factory=list)
    city: str = ""
    district: str = ""
    description: str = ""
    budget_amount: str = ""
    budget_unit: str = ""
    timing: str = "flexible"
    timing_date: str = ""
    address: str = ""


# --- masters' side: feed and responses (spec D3, D10.9) ----------------------------------------


class ResponseInput(BaseModel):
    """ "Ready to take it": an optional message (≤ 500) and an optional price (amount + unit).

    Types only, like `RequestInput`; the rules live in the responses service.
    """

    message: str | None = None
    price_amount: Decimal | None = None
    price_unit: str | None = None


class FeedCard(BaseModel):
    """What a master sees of a request in the feed. Never the address, never client contacts."""

    id: int
    specialties: list[str]
    place: str
    description: str
    budget_amount: Decimal | None
    budget_unit: str | None
    timing: str
    timing_date: date | None
    created_at: datetime
    photos: list[PhotoView] = Field(default_factory=list)


class FeedPage(BaseModel):
    """`eligible` is False for a user who is not a visible master: they get no requests at all."""

    eligible: bool
    items: list[FeedCard] = Field(default_factory=list)
    next_cursor: str | None = None


MyResponseStatus = Literal["open", "other_chosen", "chosen", "closed", "cancelled", "unavailable"]


class MyResponseCard(BaseModel):
    """One of the master's active responses with the request's state for them (spec D10.9).

    For "unavailable" (hidden by moderation, or the client's account is gone) every detail of
    the request is left out: the master only learns that it is no longer available.
    """

    request_id: int
    status: MyResponseStatus
    responded_at: datetime
    message: str | None
    price_amount: Decimal | None
    price_unit: str | None
    specialties: list[str] = Field(default_factory=list)
    place: str | None = None
    description: str | None = None
    thumb_url: str | None = None


# --- lifecycle: choosing a master (spec D4, D10.1, D10.5) --------------------------------------


class ClientResponseCard(BaseModel):
    """One active response on the client's own request: the master card with their contacts.

    The client is signed in, so they get the same contacts as on the master's profile (D4);
    `contacts` is None for an inactive (blocked or deleted) viewer, as on profiles.
    `published` False means the master unpublished: no profile link, but still choosable.
    """

    response_id: int
    slug: str
    display_name: str
    avatar_url: str | None
    headline: str | None
    published: bool
    responded_at: datetime
    message: str | None
    price_amount: Decimal | None
    price_unit: str | None
    contacts: ContactsView | None
    chosen: bool
    choosable: bool


class ClientContactView(BaseModel):
    """What the chosen master learns about the client: the name and the contact phone.

    Never the email, the Telegram id or the address (spec D4, D10.1).
    """

    name: str | None
    phone: str


class MasterRequestView(BaseModel):
    """A request as one master may see it (spec D4, D10.9). Never the address.

    `status` is the request's state for this master. For "unavailable" (hidden by moderation,
    or the client's account is gone) every detail is left out: only the id and the status.
    `contact` is the client's name and phone, for the chosen master only.
    """

    id: int
    status: MyResponseStatus
    request_status: str | None = None
    specialties: list[str] = Field(default_factory=list)
    place: str | None = None
    description: str | None = None
    budget_amount: Decimal | None = None
    budget_unit: str | None = None
    timing: str | None = None
    timing_date: date | None = None
    created_at: datetime | None = None
    photos: list[PhotoView] = Field(default_factory=list)
    responded: bool = False
    message: str | None = None
    price_amount: Decimal | None = None
    price_unit: str | None = None
    responded_at: datetime | None = None
    contact: ClientContactView | None = None
    can_respond: bool = False
    can_withdraw: bool = False
