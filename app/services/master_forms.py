"""Read models for the master onboarding and edit forms (no writes here)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import City, District, MasterArea, MasterProfile, MasterSpecialty, Specialty
from app.models.catalog import Category
from app.schemas.master import (
    BasicsValues,
    CategoryOption,
    CityOption,
    ContactsValues,
    DistrictOption,
    ProfileState,
    SpecialtyOption,
)
from app.services.masters import missing_for_publish
from app.services.storage import get_storage


def _pick(row: Category | Specialty | City | District, lang: str) -> str:
    return (row.name_tg if lang == "tg" else row.name_ru) or row.name_ru


def basics_values(master: MasterProfile) -> BasicsValues:
    return BasicsValues(
        display_name=master.display_name,
        slug=master.slug,
        headline=master.headline or "",
        experience_years="" if master.experience_years is None else str(master.experience_years),
        is_available=master.is_available,
        bio=master.bio or "",
    )


def contacts_values(master: MasterProfile) -> ContactsValues:
    return ContactsValues(
        telegram=master.contact_telegram or "",
        whatsapp=master.contact_whatsapp or "",
        instagram=master.contact_instagram or "",
    )


def specialty_options(db: Session, master: MasterProfile | None, lang: str) -> list[CategoryOption]:
    """Active catalog only, grouped by direction. Inactive picks are never listed or ticked."""
    chosen: dict[int, bool] = {}
    if master is not None:
        chosen = dict(
            db.execute(
                select(MasterSpecialty.specialty_id, MasterSpecialty.is_primary).where(
                    MasterSpecialty.master_id == master.id
                )
            ).all()
        )
    rows = db.execute(
        select(Category, Specialty)
        .join(Specialty, Specialty.category_id == Category.id)
        .where(Category.is_active, Specialty.is_active)
        .order_by(Category.sort_order, Category.id, Specialty.sort_order, Specialty.id)
    ).all()
    groups: dict[int, CategoryOption] = {}
    for category, specialty in rows:
        group = groups.setdefault(
            category.id, CategoryOption(name=_pick(category, lang), specialties=[])
        )
        group.specialties.append(
            SpecialtyOption(
                id=specialty.id,
                name=_pick(specialty, lang),
                selected=specialty.id in chosen,
                primary=chosen.get(specialty.id, False),
            )
        )
    return list(groups.values())


def area_options(db: Session, master: MasterProfile | None, lang: str) -> list[CityOption]:
    whole: set[int] = set()
    parts: set[int] = set()
    if master is not None:
        for city_id, district_id in db.execute(
            select(MasterArea.city_id, MasterArea.district_id).where(
                MasterArea.master_id == master.id
            )
        ):
            if district_id is None:
                whole.add(city_id)
            else:
                parts.add(district_id)
    cities = db.scalars(select(City).where(City.is_active).order_by(City.sort_order, City.id)).all()
    districts = db.scalars(
        select(District).where(District.is_active).order_by(District.sort_order, District.id)
    ).all()
    by_city: dict[int, list[DistrictOption]] = {}
    for d in districts:
        by_city.setdefault(d.city_id, []).append(
            DistrictOption(id=d.id, name=_pick(d, lang), selected=d.id in parts)
        )
    return [
        CityOption(
            id=c.id, name=_pick(c, lang), whole=c.id in whole, districts=by_city.get(c.id, [])
        )
        for c in cities
    ]


def profile_state(db: Session, master: MasterProfile) -> ProfileState:
    return ProfileState(
        slug=master.slug,
        avatar_url=get_storage().url(master.avatar_key) if master.avatar_key else None,
        published=master.published_at is not None,
        missing=[f"master.missing_{m}" for m in missing_for_publish(db, master)],
    )
