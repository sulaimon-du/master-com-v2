import io
import threading
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import MasterProfile, PortfolioCollection, PortfolioPhoto, User
from app.services import portfolio
from app.services.images import ImageError
from app.services.masters import BasicsInput, create_profile
from app.services.portfolio import PortfolioError
from app.services.storage import LocalDiskStorage, get_storage, new_key


def _master(db: Session) -> MasterProfile:
    n = uuid.uuid4().hex[:10]
    user = User(email=f"{n}@example.com", name="Иван")
    db.add(user)
    db.flush()
    data = BasicsInput(
        display_name="Иван Петров",
        slug=f"m-{n}",
        headline=None,
        experience_years=1,
        is_available=True,
    )
    return create_profile(db, user, data)


def _png(color=(200, 30, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _code(exc: pytest.ExceptionInfo[PortfolioError]) -> str:
    return exc.value.code


def _col(db: Session, master: MasterProfile, title: str = "Кухня") -> PortfolioCollection:
    return portfolio.create_collection(db, master, title, None)


def _seed_photo(db: Session, collection_id: int, position: int, **kw) -> PortfolioPhoto:
    key = new_key("p")
    photo = PortfolioPhoto(
        collection_id=collection_id,
        storage_key=key,
        thumb_key=new_key("p", thumb_of=key),
        width=10,
        height=10,
        position=position,
        **kw,
    )
    db.add(photo)
    db.flush()
    return photo


def test_create_edit_hide_collection(db: Session) -> None:
    master = _master(db)
    col = portfolio.create_collection(db, master, "  Ванная  ", "  описание ")
    assert (col.title, col.description) == ("Ванная", "описание")
    portfolio.update_collection(db, master, col.id, title="Санузел", description="  ")
    db.refresh(col)
    assert (col.title, col.description) == ("Санузел", None)
    portfolio.set_collection_hidden(db, master, col.id, True)
    assert col.hidden_at is not None
    portfolio.set_collection_hidden(db, master, col.id, False)
    assert col.hidden_at is None


@pytest.mark.parametrize(("title", "desc", "code"), [
    ("   ", None, "title"),
    ("x" * 121, None, "title"),
    ("ok", "d" * 1001, "description"),
])  # fmt: skip
def test_collection_validation(db: Session, title: str, desc: str | None, code: str) -> None:
    master = _master(db)
    with pytest.raises(PortfolioError) as exc:
        portfolio.create_collection(db, master, title, desc)
    assert _code(exc) == code
    col = _col(db, master)
    with pytest.raises(PortfolioError) as exc:
        portfolio.update_collection(db, master, col.id, title=title, description=desc)
    assert _code(exc) == code


def test_collection_limit_30(db: Session) -> None:
    master = _master(db)
    for i in range(get_settings().limit("collections")):
        portfolio.create_collection(db, master, f"c{i}", None)
    with pytest.raises(PortfolioError) as exc:
        portfolio.create_collection(db, master, "one more", None)
    assert _code(exc) == "limit"


def test_moves_swap_positions_and_stay_dense(db: Session) -> None:
    master = _master(db)
    a, b, c = (_col(db, master, t) for t in "abc")
    assert [a.position, b.position, c.position] == [0, 1, 2]
    portfolio.move_collection(db, master, a.id, "down")
    portfolio.move_collection(db, master, c.id, "up")
    assert [x.title for x in portfolio.collections_for_owner(db, master)] == ["b", "c", "a"]
    portfolio.move_collection(db, master, b.id, "up")  # first: no-op
    portfolio.move_collection(db, master, a.id, "down")  # last: no-op
    cols = db.scalars(select(PortfolioCollection).order_by(PortfolioCollection.position)).all()
    assert sorted(x.position for x in cols if x.master_id == master.id) == [0, 1, 2]

    storage = get_storage()
    col = a
    p1, p2, p3 = (portfolio.add_photo(db, storage, master, col.id, _png()) for _ in range(3))
    assert [p1.position, p2.position, p3.position] == [0, 1, 2]
    portfolio.move_photo(db, master, p1.id, "right")
    portfolio.move_photo(db, master, p3.id, "left")
    ids = [p.id for p in portfolio.collection_photos(db, master, col.id, owner=True)]
    assert ids == [p2.id, p3.id, p1.id]
    portfolio.move_photo(db, master, p2.id, "left")  # no-op
    ids2 = [p.id for p in portfolio.collection_photos(db, master, col.id, owner=True)]
    assert ids2 == ids


def test_photo_move_skips_deleted_neighbour(db: Session) -> None:
    master = _master(db)
    col = _col(db, master)
    p1, p2, p3 = (portfolio.add_photo(db, get_storage(), master, col.id, _png()) for _ in range(3))
    portfolio.delete_photo(db, master, p2.id)
    portfolio.move_photo(db, master, p1.id, "right")
    ids = [p.id for p in portfolio.collection_photos(db, master, col.id, owner=True)]
    assert ids == [p3.id, p1.id]


def test_add_photo_writes_both_files(db: Session) -> None:
    master = _master(db)
    col = _col(db, master)
    storage = get_storage()
    photo = portfolio.add_photo(db, storage, master, col.id, _png(size=(2000, 1000)))
    assert isinstance(storage, LocalDiskStorage)
    assert storage.path(photo.storage_key).exists()
    assert storage.path(photo.thumb_key).exists()
    assert photo.thumb_key == new_key("p", thumb_of=photo.storage_key)
    assert (photo.width, photo.height) == (1600, 800)
    with pytest.raises(ImageError):
        portfolio.add_photo(db, storage, master, col.id, b"not an image")


def test_deleted_photo_excluded_and_undo_restores(db: Session) -> None:
    master = _master(db)
    col = _col(db, master)
    storage = get_storage()
    p1 = portfolio.add_photo(db, storage, master, col.id, _png())
    p2 = portfolio.add_photo(db, storage, master, col.id, _png())
    portfolio.delete_photo(db, master, p1.id)
    assert p1.deleted_at is not None
    assert [p.id for p in portfolio.collection_photos(db, master, col.id, owner=True)] == [p2.id]
    assert portfolio.collections_for_owner(db, master)[0].photo_count == 1
    assert portfolio.public_collections(db, master)[0].photo_count == 1
    with pytest.raises(PortfolioError) as exc:
        portfolio.delete_photo(db, master, p1.id)
    assert _code(exc) == "not_found"
    portfolio.undo_delete_photo(db, master, p1.id)
    assert p1.deleted_at is None
    ids = [p.id for p in portfolio.collection_photos(db, master, col.id, owner=True)]
    assert ids == [p1.id, p2.id]  # back in its old slot
    assert storage.path(p1.storage_key).exists()


def test_undo_expires_after_30_days(db: Session) -> None:
    master = _master(db)
    col = _col(db, master)
    photo = portfolio.add_photo(db, get_storage(), master, col.id, _png())
    portfolio.delete_photo(db, master, photo.id)
    photo.deleted_at = utcnow() - timedelta(days=30, minutes=1)
    db.flush()
    with pytest.raises(PortfolioError) as exc:
        portfolio.undo_delete_photo(db, master, photo.id)
    assert _code(exc) == "undo_expired"


def test_undo_refused_when_limit_full(db: Session) -> None:
    master = _master(db)
    col = _col(db, master)
    limit = get_settings().limit("photos_per_collection")
    for i in range(limit):
        _seed_photo(db, col.id, i)
    gone = _seed_photo(db, col.id, limit, deleted_at=utcnow())
    with pytest.raises(PortfolioError) as exc:
        portfolio.undo_delete_photo(db, master, gone.id)
    assert _code(exc) == "limit"
    with pytest.raises(PortfolioError) as exc:
        portfolio.add_photo(db, get_storage(), master, col.id, _png())
    assert _code(exc) == "limit"


def test_public_collections_rules(db: Session) -> None:
    master = _master(db)
    shown = _col(db, master, "shown")
    hidden = _col(db, master, "hidden")
    empty = _col(db, master, "empty")
    only_deleted = _col(db, master, "deleted")
    first = _seed_photo(db, shown.id, 0)
    _seed_photo(db, shown.id, 1)
    _seed_photo(db, shown.id, 2, hidden_at=utcnow())
    _seed_photo(db, hidden.id, 0)
    _seed_photo(db, only_deleted.id, 0, deleted_at=utcnow())
    portfolio.set_collection_hidden(db, master, hidden.id, True)
    cards = portfolio.public_collections(db, master)
    assert [c.title for c in cards] == ["shown"]
    assert cards[0].photo_count == 2
    assert cards[0].cover_thumb_url == get_storage().url(first.thumb_key)
    assert not cards[0].hidden
    owner = {c.title: c for c in portfolio.collections_for_owner(db, master)}
    assert set(owner) == {"shown", "hidden", "empty", "deleted"}
    assert owner["hidden"].hidden
    assert owner["empty"].cover_thumb_url is None
    assert owner["deleted"].photo_count == 0
    assert empty.id and only_deleted.id
    with pytest.raises(PortfolioError) as exc:
        portfolio.collection_photos(db, master, hidden.id, owner=False)
    assert _code(exc) == "not_found"
    pub = portfolio.collection_photos(db, master, shown.id, owner=False)
    assert len(pub) == 2
    assert pub[0].url == get_storage().url(first.storage_key)


def test_foreign_collection_and_photo_ids_are_not_found(db: Session) -> None:
    a, b = _master(db), _master(db)
    col = _col(db, a)
    photo = portfolio.add_photo(db, get_storage(), a, col.id, _png())
    deleted = portfolio.add_photo(db, get_storage(), a, col.id, _png())
    portfolio.delete_photo(db, a, deleted.id)
    calls = [
        lambda: portfolio.update_collection(db, b, col.id, title="x", description=None),
        lambda: portfolio.set_collection_hidden(db, b, col.id, True),
        lambda: portfolio.move_collection(db, b, col.id, "up"),
        lambda: portfolio.add_photo(db, get_storage(), b, col.id, _png()),
        lambda: portfolio.move_photo(db, b, photo.id, "left"),
        lambda: portfolio.delete_photo(db, b, photo.id),
        lambda: portfolio.undo_delete_photo(db, b, deleted.id),
        lambda: portfolio.collection_photos(db, b, col.id, owner=True),
        lambda: portfolio.collection_photos(db, b, col.id, owner=False),
    ]
    for call in calls:
        with pytest.raises(PortfolioError) as exc:
            call()
        assert _code(exc) == "not_found"
    assert portfolio.collections_for_owner(db, b) == []


def test_files_removed_when_db_write_fails(db: Session, monkeypatch) -> None:
    master = _master(db)
    col = _col(db, master)
    storage = get_storage()
    assert isinstance(storage, LocalDiskStorage)
    written: list[str] = []
    real_put = storage.put

    def put(key: str, data: bytes, content_type: str) -> None:
        real_put(key, data, content_type)
        written.append(key)

    monkeypatch.setattr(storage, "put", put)
    real_flush = db.flush

    def flush(*args, **kwargs):
        if written:
            raise IntegrityError("insert", {}, Exception("boom"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", flush)
    with pytest.raises(IntegrityError):
        portfolio.add_photo(db, storage, master, col.id, _png())
    assert len(written) == 2
    assert all(not storage.path(k).exists() for k in written)


def test_photo_limit_60_with_concurrent_uploads(migrated_engine) -> None:
    factory = get_sessionmaker()
    storage = get_storage()
    limit = get_settings().limit("photos_per_collection")
    with factory() as s:
        master = _master(s)
        col = _col(s, master)
        for i in range(limit - 1):
            _seed_photo(s, col.id, i)
        s.commit()
        user_id, master_id, col_id = master.user_id, master.id, col.id
    barrier = threading.Barrier(2)
    results: list[str] = []

    def upload() -> None:
        with factory() as s:
            m = s.get(MasterProfile, master_id)
            barrier.wait()
            try:
                portfolio.add_photo(s, storage, m, col_id, _png())
                s.commit()
                results.append("ok")
            except PortfolioError as exc:
                s.rollback()
                results.append(exc.code)

    try:
        threads = [threading.Thread(target=upload) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(results) == ["limit", "ok"]
        with factory() as s:
            count = s.query(PortfolioPhoto).filter_by(collection_id=col_id).count()
            assert count == limit
    finally:
        with factory() as s:
            s.execute(delete(MasterProfile).where(MasterProfile.id == master_id))
            s.execute(delete(User).where(User.id == user_id))
            s.commit()
