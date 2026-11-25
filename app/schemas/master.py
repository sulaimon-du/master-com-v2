"""Master profile input and output models; templates never get ORM rows."""

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.services.masters import BasicsInput


class BasicsForm(BaseModel):
    """Step 1 of onboarding and the profile edit form."""

    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=80)
    slug: str = Field(max_length=40)
    headline: str | None = Field(default=None, max_length=80)
    experience_years: int | None = Field(default=None, ge=0, le=60)
    is_available: bool = True
    bio: str | None = Field(default=None, max_length=1000)

    @field_validator("headline", "bio", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def to_input(self) -> "BasicsInput":
        from app.services.masters import BasicsInput

        return BasicsInput(
            display_name=self.display_name,
            slug=self.slug,
            headline=self.headline,
            experience_years=self.experience_years,
            is_available=self.is_available,
        )


class SpecialtyView(BaseModel):
    name: str
    primary: bool


class ContactsView(BaseModel):
    phone: str | None
    telegram: str | None
    whatsapp: str | None
    instagram: str | None


class CollectionCard(BaseModel):
    id: int
    title: str
    description: str | None
    cover_thumb_url: str | None
    photo_count: int
    hidden: bool


class PhotoView(BaseModel):
    id: int
    url: str
    thumb_url: str
    width: int
    height: int
    position: int
    deleted: bool


class ProfileView(BaseModel):
    """Everything the public profile page shows. `contacts` is None for guests."""

    slug: str
    display_name: str
    avatar_url: str | None
    headline: str | None
    bio: str | None
    experience_years: int | None
    is_available: bool
    since: date
    specialties: list[SpecialtyView]
    areas: list[str]
    city: str | None = None  # the first working city, for the page title
    contacts: ContactsView | None
    is_draft: bool
    collections: list[CollectionCard] = []


class SpecialtyOption(BaseModel):
    id: int
    name: str
    selected: bool
    primary: bool


class CategoryOption(BaseModel):
    name: str
    specialties: list[SpecialtyOption]


class DistrictOption(BaseModel):
    id: int
    name: str
    selected: bool


class CityOption(BaseModel):
    id: int
    name: str
    whole: bool
    districts: list[DistrictOption]


class BasicsValues(BaseModel):
    """What the basics form shows: the saved values, or the ones just submitted."""

    display_name: str = ""
    slug: str = ""
    headline: str = ""
    experience_years: str = ""
    is_available: bool = True
    bio: str = ""


class ContactsValues(BaseModel):
    telegram: str = ""
    whatsapp: str = ""
    instagram: str = ""


class ProfileState(BaseModel):
    """The owner's own status line: public address, avatar and what blocks publishing."""

    slug: str
    avatar_url: str | None
    published: bool
    missing: list[str]
