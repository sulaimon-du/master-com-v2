"""Search input and output models (spec D2, D3). Cards carry no contacts and no ORM rows."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

Sort = Literal["relevance", "rating", "newest"]
# A piece of a display string and whether it matched the query; templates wrap matches in
# <mark> with autoescape, so a segment is always plain text.
Segment = tuple[str, bool]


@dataclass(frozen=True)
class SearchQuery:
    """What the URL asked for, already cleaned; slugs are resolved by the search itself."""

    q: str | None
    specialty: str | None
    category: str | None
    city: str | None
    district: str | None
    available: bool
    sort: Sort | None
    page: int


class MasterCard(BaseModel):
    slug: str
    display_name: list[Segment]
    headline: list[Segment]
    avatar_url: str | None
    specialties: list[list[Segment]]  # primary first, at most 3
    area: str
    rating: float | None
    photos: list[str]  # at most 4 thumbnail URLs
    is_available: bool


class Suggestion(BaseModel):
    """The "did you mean" offer: one specialty or place whose filter the link sets."""

    kind: Literal["specialty", "city", "district"]
    slug: str
    name: str
    city_slug: str | None = None  # for a district, the city it belongs to


class SpecialtyHint(BaseModel):
    """The query names a specialty: offer to filter by it (D11.3). It never filters by itself."""

    slug: str
    name: str


class SearchResult(BaseModel):
    items: list[MasterCard]
    total: int
    page: int
    pages: int
    suggestion: Suggestion | None = None
    specialty_hint: SpecialtyHint | None = None
    timed_out: bool = False
    # Codes of URL values that were ignored because no active row has that slug.
    notices: list[str] = []


class Choice(BaseModel):
    """A catalog or place entry for a select box or a pill: its URL slug and display name."""

    slug: str
    name: str


class CategoryChoice(Choice):
    specialties: list[Choice]


class SearchOptions(BaseModel):
    categories: list[CategoryChoice]
    cities: list[Choice]
    districts: list[Choice]  # of the selected city only; district slugs are unique per city


class HomeMasters(BaseModel):
    """The home page's masters block (R-028): city chips, the filters that took effect, cards."""

    cities: list[Choice]  # the top cities by visible masters, most first
    city: str | None  # one of `cities`, or None for "all"
    available: bool
    items: list[MasterCard]
