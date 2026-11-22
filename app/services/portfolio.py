"""Portfolio rules: collections, photos, limits, order and what the public sees.

Every id from a URL is resolved *through* the master, so a foreign id is "not found".
Lock order: the master row is never locked; a collection row is locked before its photo rows.
Callers commit.
"""

import contextlib
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utcnow
from app.models import MasterProfile, PortfolioCollection, PortfolioPhoto
from app.schemas.master import CollectionCard, PhotoView
from app.services.images import process_portfolio
from app.services.storage import Storage, get_storage, new_key

PortfolioCode = Literal["limit", "not_found", "title", "description", "undo_expired"]

UNDO_WINDOW = timedelta(days=30)


class PortfolioError(Exception):
    def __init__(self, code: PortfolioCode) -> None:
        super().__init__(code)
        self.code = code


# --- lookups ----------------------------------------------------------------------------------


def _collection(
    db: Session, master: MasterProfile, collection_id: int, *, lock: bool = False
) -> PortfolioCollection:
    stmt = select(PortfolioCollection).where(
        PortfolioCollection.id == collection_id, PortfolioCollection.master_id == master.id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    collection = db.scalar(stmt)
    if collection is None:
        raise PortfolioError("not_found")
    return collection


def _photo(db: Session, master: MasterProfile, photo_id: int, *, lock: bool = False):
    """The photo and its collection; with `lock` the collection row is locked first."""
    collection_id = db.scalar(
        select(PortfolioPhoto.collection_id)
        .join(PortfolioCollection, PortfolioCollection.id == PortfolioPhoto.collection_id)
        .where(PortfolioPhoto.id == photo_id, PortfolioCollection.master_id == master.id)
    )
    if collection_id is None:
        raise PortfolioError("not_found")
    collection = _collection(db, master, collection_id, lock=lock)
    stmt = select(PortfolioPhoto).where(
        PortfolioPhoto.id == photo_id, PortfolioPhoto.collection_id == collection.id
    )
    if lock:
        stmt = stmt.execution_options(populate_existing=True)
    photo = db.scalar(stmt)
    if photo is None:
        raise PortfolioError("not_found")
    return photo, collection


def photo_collection_id(db: Session, master: MasterProfile, photo_id: int) -> int:
    """The collection a photo of this master is in (deleted or not); foreign ids are not found."""
    _, collection = _photo(db, master, photo_id)
    return collection.id


def _live_count(db: Session, collection_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).where(
                PortfolioPhoto.collection_id == collection_id, PortfolioPhoto.deleted_at.is_(None)
            )
        )
        or 0
    )


def _next_position(db: Session, column, *where) -> int:
    top = db.scalar(select(func.max(column)).where(*where))
    return 0 if top is None else top + 1


# --- collections ------------------------------------------------------------------------------


def _clean_text(title: str, description: str | None) -> tuple[str, str | None]:
    title = title.strip()
    if not 1 <= len(title) <= 120:
        raise PortfolioError("title")
    description = (description or "").strip() or None
    if description is not None and len(description) > 1000:
        raise PortfolioError("description")
    return title, description


def create_collection(
    db: Session, master: MasterProfile, title: str, description: str | None
) -> PortfolioCollection:
    title, description = _clean_text(title, description)
    count = db.scalar(select(func.count()).where(PortfolioCollection.master_id == master.id))
    if (count or 0) >= get_settings().limit("collections"):
        raise PortfolioError("limit")
    collection = PortfolioCollection(
        master_id=master.id,
        title=title,
        description=description,
        position=_next_position(
            db, PortfolioCollection.position, PortfolioCollection.master_id == master.id
        ),
    )
    db.add(collection)
    db.flush()
    return collection


def update_collection(
    db: Session, master: MasterProfile, collection_id: int, *, title: str, description: str | None
) -> None:
    collection = _collection(db, master, collection_id)
    collection.title, collection.description = _clean_text(title, description)
    db.flush()


def set_collection_hidden(
    db: Session, master: MasterProfile, collection_id: int, hidden: bool
) -> None:
    collection = _collection(db, master, collection_id)
    collection.hidden_at = utcnow() if hidden else None
    db.flush()


def move_collection(
    db: Session, master: MasterProfile, collection_id: int, direction: Literal["up", "down"]
) -> None:
    collection = _collection(db, master, collection_id)
    stmt = select(PortfolioCollection).where(PortfolioCollection.master_id == master.id)
    if direction == "up":
        stmt = stmt.where(PortfolioCollection.position < collection.position).order_by(
            PortfolioCollection.position.desc()
        )
    else:
        stmt = stmt.where(PortfolioCollection.position > collection.position).order_by(
            PortfolioCollection.position
        )
    neighbour = db.scalars(stmt.limit(1)).first()
    if neighbour is None:
        return
    collection.position, neighbour.position = neighbour.position, collection.position
    db.flush()


# --- photos -----------------------------------------------------------------------------------


def add_photo(
    db: Session, storage: Storage, master: MasterProfile, collection_id: int, data: bytes
) -> PortfolioPhoto:
    _collection(db, master, collection_id)  # cheap ownership check before heavy work
    full, thumb = process_portfolio(data)  # ImageError propagates to the web layer
    # Two uploads at the limit must not both pass the count: serialise on the collection row.
    collection = _collection(db, master, collection_id, lock=True)
    if _live_count(db, collection.id) >= get_settings().limit("photos_per_collection"):
        raise PortfolioError("limit")
    key = new_key("p")
    thumb_key = new_key("p", thumb_of=key)
    written: list[str] = []
    try:
        storage.put(key, full.data, "image/webp")
        written.append(key)
        storage.put(thumb_key, thumb.data, "image/webp")
        written.append(thumb_key)
        photo = PortfolioPhoto(
            collection_id=collection.id,
            storage_key=key,
            thumb_key=thumb_key,
            width=full.width,
            height=full.height,
            position=_next_position(
                db, PortfolioPhoto.position, PortfolioPhoto.collection_id == collection.id
            ),
        )
        with db.begin_nested():
            db.add(photo)
            db.flush()
    except BaseException:
        for written_key in written:
            # Cleanup must not hide the original error.
            with contextlib.suppress(Exception):
                storage.delete(written_key)
        raise
    return photo


def move_photo(
    db: Session, master: MasterProfile, photo_id: int, direction: Literal["left", "right"]
) -> None:
    photo, collection = _photo(db, master, photo_id)
    if photo.deleted_at is not None:
        raise PortfolioError("not_found")
    stmt = select(PortfolioPhoto).where(
        PortfolioPhoto.collection_id == collection.id, PortfolioPhoto.deleted_at.is_(None)
    )
    if direction == "left":
        stmt = stmt.where(PortfolioPhoto.position < photo.position).order_by(
            PortfolioPhoto.position.desc()
        )
    else:
        stmt = stmt.where(PortfolioPhoto.position > photo.position).order_by(
            PortfolioPhoto.position
        )
    neighbour = db.scalars(stmt.limit(1)).first()
    if neighbour is None:
        return
    photo.position, neighbour.position = neighbour.position, photo.position
    db.flush()


def delete_photo(db: Session, master: MasterProfile, photo_id: int) -> None:
    """Soft delete; the files stay so the delete can be undone (a purge job removes old ones)."""
    photo, _ = _photo(db, master, photo_id)
    if photo.deleted_at is not None:
        raise PortfolioError("not_found")
    photo.deleted_at = utcnow()
    db.flush()


def undo_delete_photo(db: Session, master: MasterProfile, photo_id: int) -> None:
    photo, collection = _photo(db, master, photo_id, lock=True)
    if photo.deleted_at is None:
        raise PortfolioError("not_found")
    if utcnow() - photo.deleted_at > UNDO_WINDOW:
        raise PortfolioError("undo_expired")
    if _live_count(db, collection.id) >= get_settings().limit("photos_per_collection"):
        raise PortfolioError("limit")
    photo.deleted_at = None
    db.flush()


# --- views ------------------------------------------------------------------------------------


def _cards(db: Session, master: MasterProfile, *, public: bool) -> list[CollectionCard]:
    storage = get_storage()
    stmt = (
        select(PortfolioCollection)
        .where(PortfolioCollection.master_id == master.id)
        .order_by(PortfolioCollection.position, PortfolioCollection.id)
    )
    if public:
        stmt = stmt.where(PortfolioCollection.hidden_at.is_(None))
    cards: list[CollectionCard] = []
    for collection in db.scalars(stmt):
        photos = select(PortfolioPhoto).where(
            PortfolioPhoto.collection_id == collection.id, PortfolioPhoto.deleted_at.is_(None)
        )
        if public:
            photos = photos.where(PortfolioPhoto.hidden_at.is_(None))
        rows = db.scalars(photos.order_by(PortfolioPhoto.position, PortfolioPhoto.id)).all()
        if public and not rows:
            continue
        cards.append(
            CollectionCard(
                id=collection.id,
                title=collection.title,
                description=collection.description,
                cover_thumb_url=storage.url(rows[0].thumb_key) if rows else None,
                photo_count=len(rows),
                hidden=collection.hidden_at is not None,
            )
        )
    return cards


def collections_for_owner(db: Session, master: MasterProfile) -> list[CollectionCard]:
    return _cards(db, master, public=False)


def public_collections(db: Session, master: MasterProfile) -> list[CollectionCard]:
    return _cards(db, master, public=True)


def collection_photos(
    db: Session, master: MasterProfile, collection_id: int, *, owner: bool
) -> list[PhotoView]:
    collection = _collection(db, master, collection_id)
    if not owner and collection.hidden_at is not None:
        raise PortfolioError("not_found")
    stmt = select(PortfolioPhoto).where(
        PortfolioPhoto.collection_id == collection.id, PortfolioPhoto.deleted_at.is_(None)
    )
    if not owner:
        stmt = stmt.where(PortfolioPhoto.hidden_at.is_(None))
    storage = get_storage()
    return [
        PhotoView(
            id=p.id,
            url=storage.url(p.storage_key),
            thumb_url=storage.url(p.thumb_key),
            width=p.width,
            height=p.height,
            position=p.position,
            deleted=p.deleted_at is not None,
        )
        for p in db.scalars(stmt.order_by(PortfolioPhoto.position, PortfolioPhoto.id))
    ]
