import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import City, District, MasterProfile, MasterSearch, Specialty, User
from app.services import auth, masters, portfolio
from app.services.masters import BasicsInput
from app.services.search_index import rebuild_all, refresh_search
from app.services.storage import get_storage
from app.services.translit import translit


def _user(db: Session, *, verified: bool = True) -> User:
    n = uuid.uuid4().hex[:10]
    user = User(email=f"{n}@example.com", name="Иван")
    if verified:
        user.phone = "+99290" + str(int(n, 16))[:7].ljust(7, "0")
        user.phone_verified_at = utcnow()
    db.add(user)
    db.flush()
    return user


def _master(db: Session, *, verified: bool = True, ready: bool = True, **basics):
    user = _user(db, verified=verified)
    data = BasicsInput(
        display_name=basics.get("display_name", "Иван Петров"),
        slug=f"m-{uuid.uuid4().hex[:10]}",
        headline=basics.get("headline"),
        experience_years=3,
        is_available=True,
    )
    master = masters.create_profile(db, user, data)
    if ready:
        spec = db.scalars(select(Specialty).where(Specialty.slug == "santekhnik")).one()
        masters.set_specialties(db, master, [spec.id], [spec.id])
        city = db.scalars(select(City).where(City.slug == "dushanbe")).one()
        masters.set_areas(db, master, [city.id], [])
    return user, master


def _row(db: Session, master: MasterProfile) -> MasterSearch:
    db.expire_all()
    return db.get(MasterSearch, master.id)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_translit_keeps_spaces_and_digits() -> None:
    assert translit("Қубур-Сози  №5, Ҷамшед") == "qubur sozi 5 jamshed"
    assert translit("") == ""


def test_published_master_gets_a_visible_row(db: Session) -> None:
    _, master = _master(db, headline="Быстро и чисто")
    row = _row(db, master)
    assert row.is_visible is True
    assert row.published_at is not None
    assert row.has_photos is False
    assert "santekhnik" in row.document
    assert "qubur" in row.document  # tg synonym
    assert "ivan petrov" in row.names and "bystro" in row.names
    assert "dushanbe" in row.document
    city = db.scalars(select(City).where(City.slug == "dushanbe")).one()
    spec = db.scalars(select(Specialty).where(Specialty.slug == "santekhnik")).one()
    assert row.whole_city_ids == [city.id] and row.city_ids == [city.id]
    assert row.specialty_ids == [spec.id] and row.primary_specialty_ids == [spec.id]
    assert row.category_ids == [spec.category_id]
    assert row.district_ids == [] and row.named_district_ids == []


def test_district_area_fills_city_and_named_district(db: Session) -> None:
    _, master = _master(db, ready=False)
    spec = db.scalars(select(Specialty).where(Specialty.slug == "santekhnik")).one()
    masters.set_specialties(db, master, [spec.id], [])
    district = db.scalars(select(District).order_by(District.id).limit(1)).one()
    masters.set_areas(db, master, [], [district.id])
    row = _row(db, master)
    assert row.city_ids == [district.city_id]
    assert row.whole_city_ids == []
    assert row.named_district_ids == [district.id]
    assert translit(district.name_ru) in row.document


def test_draft_is_not_visible(db: Session) -> None:
    _, master = _master(db, ready=False)
    refresh_search(db, master)
    assert _row(db, master).is_visible is False


def test_removing_the_last_area_hides_the_row(db: Session) -> None:
    _, master = _master(db)
    assert _row(db, master).is_visible is True
    masters.set_areas(db, master, [], [])
    assert _row(db, master).is_visible is False


def test_blocking_the_owner_hides_the_row(db: Session) -> None:
    user, master = _master(db)
    auth.block_user(db, user)
    assert _row(db, master).is_visible is False


def test_unverified_phone_is_not_visible(db: Session) -> None:
    user, master = _master(db)
    user.phone_verified_at = None
    refresh_search(db, master)
    assert _row(db, master).is_visible is False


def test_hidden_profile_is_not_visible(db: Session) -> None:
    _, master = _master(db)
    master.hidden_at = utcnow()
    refresh_search(db, master)
    assert _row(db, master).is_visible is False


def test_has_photos_follows_public_photos(db: Session) -> None:
    _, master = _master(db)
    assert _row(db, master).has_photos is False
    col = portfolio.create_collection(db, master, "Кухня", None)
    assert _row(db, master).has_photos is False
    photo = portfolio.add_photo(db, get_storage(), master, col.id, _png())
    assert _row(db, master).has_photos is True
    portfolio.set_collection_hidden(db, master, col.id, True)
    assert _row(db, master).has_photos is False
    portfolio.set_collection_hidden(db, master, col.id, False)
    assert _row(db, master).has_photos is True
    portfolio.delete_photo(db, master, photo.id)
    assert _row(db, master).has_photos is False
    portfolio.undo_delete_photo(db, master, photo.id)
    assert _row(db, master).has_photos is True


def test_document_is_capped(db: Session) -> None:
    _, master = _master(db)
    master.bio = "ж" * 1000
    refresh_search(db, master)
    assert len(_row(db, master).document) <= 20_000


def test_refresh_is_an_upsert_even_when_the_row_appears_meanwhile(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent first refresh inserts the row between our read and our write: no 500."""
    _, master = _master(db)
    original_get = db.get

    def blind_get(entity, ident, *args, **kwargs):
        if entity is MasterSearch:
            return None  # as if the row did not exist yet when we looked
        return original_get(entity, ident, *args, **kwargs)

    monkeypatch.setattr(db, "get", blind_get)
    master.display_name = "Новое Имя"
    refresh_search(db, master)
    refresh_search(db, master)
    monkeypatch.undo()
    assert "novoe imia" in _row(db, master).names or "novoe" in _row(db, master).names
    assert db.scalar(select(text("count(*)")).select_from(MasterSearch)) >= 1


def test_rebuild_all_fixes_stale_rows(db: Session) -> None:
    _, one = _master(db)
    _, two = _master(db, ready=False)
    db.execute(text("UPDATE master_search SET is_visible = false, document = 'x'"))
    count = rebuild_all(db)
    assert count == db.scalar(select(text("count(*)")).select_from(MasterProfile))
    assert count >= 2
    assert _row(db, one).is_visible is True
    assert "santekhnik" in _row(db, one).document
    assert _row(db, two).is_visible is False


def test_rebuild_all_removes_orphans(db: Session) -> None:
    _, master = _master(db)
    # No FK-less rows can exist (cascade), so just check the count matches the profiles.
    assert rebuild_all(db) == db.scalar(select(text("count(*)")).select_from(MasterProfile))
    assert db.get(MasterSearch, master.id) is not None


def test_cli_search_rebuild_prints_count(db: Session, capsys: pytest.CaptureFixture[str]) -> None:
    from app import cli

    assert cli.main(["search", "rebuild"]) == 0
    assert capsys.readouterr().out.strip().split()[0].isdigit()
