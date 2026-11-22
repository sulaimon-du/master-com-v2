# ruff: noqa: RUF001  (the transliteration table must contain look-alike Cyrillic letters)
"""Master profile rules: slug, basics, specialties, areas, contacts and publishing.

Every function that changes what publishing depends on (specialties, areas, contacts, basics)
calls `refresh_publication` itself, so the published state cannot go stale. Callers commit.
"""

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.models import City, District, MasterArea, MasterProfile, MasterSpecialty, Specialty, User
from app.models.catalog import Category
from app.models.master import SLUG_RE
from app.schemas.master import ContactsView, ProfileView, SpecialtyView
from app.services.auth import is_active
from app.services.portfolio import public_collections
from app.services.storage import get_storage
from app.services.telegram import normalize_phone

MasterCode = Literal[
    "slug_taken",
    "slug_invalid",
    "exists",
    "limit",
    "unknown",
    "primary_not_selected",
    "area",
    "telegram",
    "whatsapp",
    "instagram",
    "name",
    "headline",
    "bio",
    "experience",
]
Missing = Literal["phone", "specialty", "area", "hidden", "inactive"]

TELEGRAM_RE = re.compile(r"[A-Za-z0-9_]{5,32}")
INSTAGRAM_RE = re.compile(r"[A-Za-z0-9._]{1,30}")

_RU = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}  # fmt: skip
_TG = {"ғ": "gh", "ӣ": "i", "қ": "q", "ӯ": "u", "ҳ": "h", "ҷ": "j"}
_TRANSLIT = {**_RU, **_TG}


class MasterError(Exception):
    """A rule was broken; `code` says which, so the page can show the matching message."""

    def __init__(self, code: MasterCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class BasicsInput:
    display_name: str
    slug: str
    headline: str | None
    experience_years: int | None
    is_available: bool


# --- slug -------------------------------------------------------------------------------------


def slugify(name: str) -> str:
    text = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return text if len(text) >= 3 else "master"


def _slug_exists(db: Session, slug: str, own_id: int | None = None) -> bool:
    stmt = select(MasterProfile.id).where(MasterProfile.slug == slug)
    if own_id is not None:
        stmt = stmt.where(MasterProfile.id != own_id)
    return db.scalar(stmt.limit(1)) is not None


def suggest_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 1
    while _slug_exists(db, slug):
        n += 1
        suffix = f"-{n}"
        slug = base[: 40 - len(suffix)].rstrip("-") + suffix
    return slug


def check_slug(
    db: Session, slug: str, *, own_id: int | None = None
) -> Literal["free", "taken", "invalid"]:
    if not re.fullmatch(SLUG_RE, slug):
        return "invalid"
    return "taken" if _slug_exists(db, slug, own_id) else "free"


# --- basics -----------------------------------------------------------------------------------


def master_for_user(db: Session, user: User) -> MasterProfile | None:
    return db.scalar(select(MasterProfile).where(MasterProfile.user_id == user.id))


def _check_basics(data: BasicsInput, bio: str | None = None) -> None:
    """Service-level guard: the form validates too, but other callers must not skip the rules."""
    if not 1 <= len(data.display_name.strip()) <= 80:
        raise MasterError("name")
    if data.headline is not None and len(data.headline) > 80:
        raise MasterError("headline")
    if bio is not None and len(bio) > 1000:
        raise MasterError("bio")
    if data.experience_years is not None and not 0 <= data.experience_years <= 60:
        raise MasterError("experience")


def _is_slug_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "uq_master_profiles_slug"


def _apply_basics(master: MasterProfile, data: BasicsInput) -> None:
    master.slug = data.slug
    master.display_name = data.display_name
    master.headline = data.headline
    master.experience_years = data.experience_years
    master.is_available = data.is_available


def create_profile(db: Session, user: User, data: BasicsInput) -> MasterProfile:
    if master_for_user(db, user) is not None:
        raise MasterError("exists")
    if not re.fullmatch(SLUG_RE, data.slug):
        raise MasterError("slug_invalid")
    _check_basics(data)
    master = MasterProfile(user_id=user.id, origin="self", published_at=None)
    _apply_basics(master, data)
    try:
        # The unique index decides; a check made earlier may be stale by now.
        with db.begin_nested():
            db.add(master)
    except IntegrityError:
        db.expire_all()
        if master_for_user(db, user) is not None:
            raise MasterError("exists") from None
        if _slug_exists(db, data.slug):
            raise MasterError("slug_taken") from None
        raise
    return master


def update_basics(
    db: Session, master: MasterProfile, data: BasicsInput, *, bio: str | None
) -> None:
    if not re.fullmatch(SLUG_RE, data.slug):
        raise MasterError("slug_invalid")
    _check_basics(data, bio)
    try:
        with db.begin_nested():
            _apply_basics(master, data)
            master.bio = bio
    except IntegrityError as exc:
        db.expire(master)
        if _is_slug_conflict(exc):
            raise MasterError("slug_taken") from None
        raise
    refresh_publication(db, master)


def set_avatar(db: Session, master: MasterProfile, key: str) -> str | None:
    """Store the new key; return the old one for the caller to delete after commit."""
    old, master.avatar_key = master.avatar_key, key
    db.flush()
    return old


# --- specialties and areas --------------------------------------------------------------------


def set_specialties(
    db: Session, master: MasterProfile, specialty_ids: list[int], primary_ids: list[int]
) -> None:
    settings = get_settings()
    ids = list(dict.fromkeys(specialty_ids))
    primary = set(primary_ids)
    if len(ids) > settings.limit("specialties") or len(primary) > settings.limit(
        "primary_specialties"
    ):
        raise MasterError("limit")
    found = set(db.scalars(select(Specialty.id).where(Specialty.id.in_(ids), Specialty.is_active)))
    if found != set(ids):
        raise MasterError("unknown")
    if not primary <= set(ids):
        raise MasterError("primary_not_selected")
    db.execute(delete(MasterSpecialty).where(MasterSpecialty.master_id == master.id))
    db.add_all(
        MasterSpecialty(master_id=master.id, specialty_id=i, is_primary=i in primary) for i in ids
    )
    db.flush()
    refresh_publication(db, master)


def set_areas(
    db: Session, master: MasterProfile, whole_city_ids: list[int], district_ids: list[int]
) -> None:
    cities = set(whole_city_ids)
    districts = set(district_ids)
    active_cities = set(db.scalars(select(City.id).where(City.id.in_(cities), City.is_active)))
    rows = db.execute(
        select(District.id, District.city_id)
        .join(City, City.id == District.city_id)
        .where(District.id.in_(districts), District.is_active, City.is_active)
    ).all()
    if active_cities != cities or {r[0] for r in rows} != districts:
        raise MasterError("area")
    # A district of a city that is already chosen as a whole adds nothing.
    areas = [MasterArea(master_id=master.id, city_id=c, district_id=None) for c in sorted(cities)]
    areas += [
        MasterArea(master_id=master.id, city_id=city_id, district_id=d)
        for d, city_id in sorted(rows)
        if city_id not in cities
    ]
    if len(areas) > get_settings().limit("areas"):
        raise MasterError("limit")
    db.execute(delete(MasterArea).where(MasterArea.master_id == master.id))
    db.add_all(areas)
    db.flush()
    refresh_publication(db, master)


# --- contacts ---------------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def set_contacts(
    db: Session,
    master: MasterProfile,
    *,
    telegram: str | None,
    whatsapp: str | None,
    instagram: str | None,
) -> None:
    tg, wa, ig = _clean(telegram), _clean(whatsapp), _clean(instagram)
    if tg is not None:
        tg = tg.removeprefix("@")
        if not TELEGRAM_RE.fullmatch(tg):
            raise MasterError("telegram")
    if wa is not None:
        wa = normalize_phone(wa)
        if wa is None:
            raise MasterError("whatsapp")
    if ig is not None:
        ig = ig.removeprefix("@")
        if not INSTAGRAM_RE.fullmatch(ig):
            raise MasterError("instagram")
    master.contact_telegram, master.contact_whatsapp, master.contact_instagram = tg, wa, ig
    db.flush()
    refresh_publication(db, master)


# --- publishing -------------------------------------------------------------------------------


def _owner(db: Session, master: MasterProfile) -> User | None:
    return db.get(User, master.user_id) if master.user_id is not None else None


def _has_verified_phone(user: User | None) -> bool:
    return user is not None and user.phone is not None and user.phone_verified_at is not None


def missing_for_publish(db: Session, master: MasterProfile) -> list[Missing]:
    user = _owner(db, master)
    missing: list[Missing] = []
    if not _has_verified_phone(user):
        missing.append("phone")
    if not db.scalar(select(func.count()).where(MasterSpecialty.master_id == master.id)):
        missing.append("specialty")
    if not db.scalar(select(func.count()).where(MasterArea.master_id == master.id)):
        missing.append("area")
    if master.hidden_at is not None:
        missing.append("hidden")
    if user is None or not is_active(user):
        missing.append("inactive")
    return missing


def refresh_publication(db: Session, master: MasterProfile) -> bool:
    """Set or clear `published_at` and keep `contact_phone` equal to the verified phone."""
    user = _owner(db, master)
    master.contact_phone = user.phone if _has_verified_phone(user) and user else None
    if missing_for_publish(db, master):
        master.published_at = None
    elif master.published_at is None:
        master.published_at = utcnow()
    db.flush()
    return master.published_at is not None


# --- public view ------------------------------------------------------------------------------


def public_master(db: Session, slug: str, viewer: User | None) -> MasterProfile | None:
    """The profile if `viewer` may see it: published ones for all, drafts for their owner."""
    master = db.scalar(select(MasterProfile).where(MasterProfile.slug == slug))
    if master is None:
        return None
    if viewer is not None and is_active(viewer) and viewer.id == master.user_id:
        return master
    if master.published_at is None or master.hidden_at is not None:
        return None
    owner = _owner(db, master)
    if master.user_id is not None and (owner is None or not is_active(owner)):
        return None
    return master


def profile_view(
    db: Session, master: MasterProfile, *, lang: str, viewer: User | None
) -> ProfileView:
    def pick(row: Category | Specialty | City | District) -> str:
        return (row.name_tg if lang == "tg" else row.name_ru) or row.name_ru

    spec_rows = db.execute(
        select(Specialty, MasterSpecialty.is_primary)
        .join(MasterSpecialty, MasterSpecialty.specialty_id == Specialty.id)
        .join(Category, Category.id == Specialty.category_id)
        .where(MasterSpecialty.master_id == master.id)
        .order_by(
            MasterSpecialty.is_primary.desc(),
            Category.sort_order,
            Specialty.sort_order,
            Specialty.id,
        )
    ).all()
    area_rows = db.execute(
        select(City, District)
        .select_from(MasterArea)
        .join(City, City.id == MasterArea.city_id)
        .outerjoin(District, District.id == MasterArea.district_id)
        .where(MasterArea.master_id == master.id)
        .order_by(City.sort_order, City.id, District.id.is_not(None), District.sort_order)
    ).all()
    areas = [pick(c) if d is None else f"{pick(c)} — {pick(d)}" for c, d in area_rows]

    contacts = None
    if viewer is not None and is_active(viewer):
        contacts = ContactsView(
            phone=master.contact_phone,
            telegram=master.contact_telegram,
            whatsapp=master.contact_whatsapp,
            instagram=master.contact_instagram,
        )
    return ProfileView(
        slug=master.slug,
        display_name=master.display_name,
        avatar_url=get_storage().url(master.avatar_key) if master.avatar_key else None,
        headline=master.headline,
        bio=master.bio,
        experience_years=master.experience_years,
        is_available=master.is_available,
        since=master.created_at.date(),
        specialties=[SpecialtyView(name=pick(s), primary=p) for s, p in spec_rows],
        areas=areas,
        contacts=contacts,
        is_draft=master.published_at is None,
        collections=public_collections(db, master),
    )
