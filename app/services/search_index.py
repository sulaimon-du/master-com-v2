"""The denormalized search row per master (spec D1).

`refresh_search` rebuilds one row from the profile; `refresh_publication` and the portfolio and
block paths call it, so the index follows every change that matters. Callers commit.
"""

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    City,
    District,
    MasterArea,
    MasterProfile,
    MasterSearch,
    MasterSpecialty,
    PortfolioCollection,
    PortfolioPhoto,
    Specialty,
    User,
)
from app.models.catalog import Category, SpecialtySynonym
from app.models.search import PlaceSynonym
from app.services.translit import translit

DOCUMENT_LIMIT = 20_000


def _has_public_photos(db: Session, master: MasterProfile) -> bool:
    """Same rule as the public profile grid: a visible collection with a visible live photo."""
    return bool(
        db.scalar(
            select(
                exists().where(
                    PortfolioCollection.master_id == master.id,
                    PortfolioCollection.hidden_at.is_(None),
                    PortfolioPhoto.collection_id == PortfolioCollection.id,
                    PortfolioPhoto.deleted_at.is_(None),
                    PortfolioPhoto.hidden_at.is_(None),
                )
            )
        )
    )


def _is_visible(master: MasterProfile, owner: User | None) -> bool:
    return (
        master.published_at is not None
        and master.hidden_at is None
        and owner is not None
        and owner.blocked_at is None
        and owner.deleted_at is None
        and owner.phone is not None
        and owner.phone_verified_at is not None
    )


def _specialty_words(db: Session, master: MasterProfile):
    rows = db.execute(
        select(Specialty, Category, MasterSpecialty.is_primary)
        .join(MasterSpecialty, MasterSpecialty.specialty_id == Specialty.id)
        .join(Category, Category.id == Specialty.category_id)
        .where(MasterSpecialty.master_id == master.id)
        .order_by(Specialty.id)
    ).all()
    words: list[str] = []
    for specialty, category, _ in rows:
        words += [specialty.name_ru, specialty.name_tg, category.name_ru, category.name_tg]
    ids = [s.id for s, _, _ in rows]
    if ids:
        words += db.scalars(
            select(SpecialtySynonym.text)
            .where(SpecialtySynonym.specialty_id.in_(ids))
            .order_by(SpecialtySynonym.id)
        )
    return rows, words


def _area_words(db: Session, master: MasterProfile):
    areas = db.execute(
        select(MasterArea.city_id, MasterArea.district_id)
        .where(MasterArea.master_id == master.id)
        .order_by(MasterArea.id)
    ).all()
    city_ids = {c for c, _ in areas}
    district_ids = {d for _, d in areas if d is not None}
    words: list[str] = []
    if city_ids:
        for city in db.scalars(select(City).where(City.id.in_(city_ids)).order_by(City.id)):
            words += [city.name_ru, city.name_tg]
    if district_ids:
        for d in db.scalars(
            select(District).where(District.id.in_(district_ids)).order_by(District.id)
        ):
            words += [d.name_ru, d.name_tg]
    if city_ids or district_ids:
        words += db.scalars(
            select(PlaceSynonym.text).where(
                PlaceSynonym.city_id.in_(city_ids) | PlaceSynonym.district_id.in_(district_ids)
            )
        )
    return areas, words


def _unique(values) -> list[int]:
    return sorted(set(values))


def refresh_search(db: Session, master: MasterProfile) -> None:
    """Upsert the search row of one master from the current state of the database."""
    owner = db.get(User, master.user_id) if master.user_id is not None else None
    spec_rows, spec_words = _specialty_words(db, master)
    areas, area_words = _area_words(db, master)

    head = [master.display_name, master.headline or "", *spec_words, *area_words]
    head_text = " ".join(t for t in (translit(w) for w in head) if t)
    bio = translit(master.bio or "")
    room = max(DOCUMENT_LIMIT - len(head_text) - 1, 0)  # the bio is what gets cut first
    document = " ".join(p for p in (head_text, bio[:room].strip()) if p)[:DOCUMENT_LIMIT]

    values = {
        "document": document,
        "names": translit(f"{master.display_name} {master.headline or ''}"),
        "is_visible": _is_visible(master, owner),
        "is_available": master.is_available,
        "has_photos": _has_public_photos(db, master),
        "rating_score": master.rating_score,
        "published_at": master.published_at,
        "specialty_ids": _unique(s.id for s, _, _ in spec_rows),
        "primary_specialty_ids": _unique(s.id for s, _, primary in spec_rows if primary),
        "category_ids": _unique(s.category_id for s, _, _ in spec_rows),
        "city_ids": _unique(c for c, _ in areas),
        "district_ids": _unique(d for _, d in areas if d is not None),
        "whole_city_ids": _unique(c for c, d in areas if d is None),
        "named_district_ids": _unique(d for _, d in areas if d is not None),
    }
    # One statement, so two first refreshes of the same master cannot collide on the key.
    insert_stmt = insert(MasterSearch).values(master_id=master.id, **values)
    db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[MasterSearch.master_id],
            set_={**{key: insert_stmt.excluded[key] for key in values}, "updated_at": func.now()},
        )
    )
    for obj in list(db.identity_map.values()):  # a loaded row is stale after the upsert
        if isinstance(obj, MasterSearch) and obj.master_id == master.id:
            db.expire(obj)


def refresh_search_for_user(db: Session, user: User) -> None:
    master = db.scalar(select(MasterProfile).where(MasterProfile.user_id == user.id))
    if master is not None:
        refresh_search(db, master)


def rebuild_all(db: Session) -> int:
    """Refresh every profile and drop rows that no longer have one; return the profile count."""
    db.execute(
        delete(MasterSearch).where(~exists().where(MasterProfile.id == MasterSearch.master_id))
    )
    count = 0
    for master in db.scalars(select(MasterProfile).order_by(MasterProfile.id)):
        refresh_search(db, master)
        count += 1
    return count
