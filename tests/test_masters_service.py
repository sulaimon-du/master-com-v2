import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import City, District, MasterProfile, Specialty, User
from app.services import masters
from app.services.masters import BasicsInput, MasterError


def _user(db: Session, *, verified: bool = True, blocked: bool = False) -> User:
    n = uuid.uuid4().hex[:10]
    user = User(
        email=f"{n}@example.com",
        name="Иван",
        phone="+992901234567" if verified else None,
        phone_verified_at=utcnow() if verified else None,
        blocked_at=utcnow() if blocked else None,
    )
    if verified:
        # Verified phones are unique; derive one per test from the id.
        user.phone = "+99290" + str(int(n, 16))[:7].ljust(7, "0")
    db.add(user)
    db.flush()
    return user


def _basics(slug: str | None = None, name: str = "Иван Петров") -> BasicsInput:
    return BasicsInput(
        display_name=name,
        slug=slug or f"m-{uuid.uuid4().hex[:10]}",
        headline=None,
        experience_years=5,
        is_available=True,
    )


def _master(db: Session, **user_kw) -> tuple[User, MasterProfile]:
    user = _user(db, **user_kw)
    return user, masters.create_profile(db, user, _basics())


def _specialties(db: Session, n: int) -> list[int]:
    return list(
        db.scalars(select(Specialty.id).where(Specialty.is_active).order_by(Specialty.id).limit(n))
    )


def _city(db: Session) -> City:
    return db.scalars(select(City).where(City.slug == "dushanbe")).one()


def _ready(db: Session, **user_kw) -> tuple[User, MasterProfile]:
    user, master = _master(db, **user_kw)
    masters.set_specialties(db, master, _specialties(db, 1), [])
    masters.set_areas(db, master, [_city(db).id], [])
    return user, master


def _code(exc: pytest.ExceptionInfo[MasterError]) -> str:
    return exc.value.code


def test_slugify_cyrillic_and_tajik() -> None:
    assert masters.slugify("Ҷамшед Қодиров") == "jamshed-qodirov"
    assert masters.slugify("Иван Петров") == "ivan-petrov"
    assert masters.slugify("Щукин Жора Ёлкин") == "shchukin-zhora-yolkin"
    assert masters.slugify("Ғулом Ӯрунов Ҳасан") == "ghulom-urunov-hasan"
    assert masters.slugify("!!") == "master"
    assert len(masters.slugify("Александр " * 10)) <= 40
    assert not masters.slugify("Александр " * 10).endswith("-")


def test_suggest_slug_adds_suffix(db: Session) -> None:
    base = masters.slugify("Тест Слагов")
    assert masters.suggest_slug(db, "Тест Слагов") == base
    masters.create_profile(db, _user(db), _basics(slug=base))
    assert masters.suggest_slug(db, "Тест Слагов") == f"{base}-2"
    masters.create_profile(db, _user(db), _basics(slug=f"{base}-2"))
    assert masters.suggest_slug(db, "Тест Слагов") == f"{base}-3"


def test_suggest_slug_suffix_keeps_within_40(db: Session) -> None:
    base = "a" * 40
    masters.create_profile(db, _user(db), _basics(slug=base))
    suggestion = masters.suggest_slug(db, "a" * 60)
    assert suggestion != base and len(suggestion) <= 40 and suggestion.endswith("-2")


def test_check_slug_states(db: Session) -> None:
    _, master = _master(db)
    assert masters.check_slug(db, "free-slug-xyz") == "free"
    assert masters.check_slug(db, master.slug) == "taken"
    assert masters.check_slug(db, master.slug, own_id=master.id) == "free"
    for bad in ("ab", "Has-Upper", "with space", "a" * 41, "под"):
        assert masters.check_slug(db, bad) == "invalid"


def test_create_profile_and_second_is_refused(db: Session) -> None:
    user = _user(db)
    master = masters.create_profile(db, user, _basics())
    assert master.origin == "self" and master.user_id == user.id and master.published_at is None
    assert masters.master_for_user(db, user) is master
    with pytest.raises(MasterError) as exc:
        masters.create_profile(db, user, _basics())
    assert _code(exc) == "exists"


def test_slug_taken_at_save_is_error_not_500(db: Session) -> None:
    # The live check said "free"; another master takes it before our save.
    slug = f"race-{uuid.uuid4().hex[:8]}"
    assert masters.check_slug(db, slug) == "free"
    masters.create_profile(db, _user(db), _basics(slug=slug))
    user = _user(db)
    with pytest.raises(MasterError) as exc:
        masters.create_profile(db, user, _basics(slug=slug))
    assert _code(exc) == "slug_taken"
    # The failed save left the session usable and created nothing.
    assert masters.master_for_user(db, user) is None
    other = masters.create_profile(db, user, _basics())
    with pytest.raises(MasterError) as exc:
        masters.update_basics(db, other, _basics(slug=slug), bio=None)
    assert _code(exc) == "slug_taken"


def test_slug_race_between_two_committed_sessions(migrated_engine) -> None:
    slug = f"race-{uuid.uuid4().hex[:8]}"
    factory = get_sessionmaker()
    ids: list[int] = []
    try:
        with factory() as a, factory() as b:
            ua, ub = _user(a), _user(b)
            a.commit()
            b.commit()
            ids = [ua.id, ub.id]
            masters.create_profile(a, ua, _basics(slug=slug))
            a.commit()
            with pytest.raises(MasterError) as exc:
                masters.create_profile(b, ub, _basics(slug=slug))
            assert _code(exc) == "slug_taken"
    finally:
        with factory() as s:
            for pid in s.scalars(select(MasterProfile.id).where(MasterProfile.user_id.in_(ids))):
                s.delete(s.get(MasterProfile, pid))
            s.flush()
            for uid in ids:
                s.delete(s.get(User, uid))
            s.commit()


def test_update_basics_and_avatar(db: Session) -> None:
    _, master = _master(db)
    data = BasicsInput("Новое Имя", master.slug, "Мастер", 7, False)
    masters.update_basics(db, master, data, bio="О себе")
    assert (master.display_name, master.headline, master.experience_years) == (
        "Новое Имя",
        "Мастер",
        7,
    )
    assert master.bio == "О себе" and master.is_available is False
    assert masters.set_avatar(db, master, "a/" + "0" * 32 + ".webp") is None
    assert masters.set_avatar(db, master, "a/" + "1" * 32 + ".webp") == "a/" + "0" * 32 + ".webp"


def test_specialty_limits(db: Session) -> None:
    _, master = _master(db)
    ids = _specialties(db, 11)
    with pytest.raises(MasterError) as exc:
        masters.set_specialties(db, master, ids, [])
    assert _code(exc) == "limit"
    with pytest.raises(MasterError) as exc:
        masters.set_specialties(db, master, ids[:5], ids[:4])
    assert _code(exc) == "limit"
    with pytest.raises(MasterError) as exc:
        masters.set_specialties(db, master, ids[:2], [ids[4]])
    assert _code(exc) == "primary_not_selected"
    with pytest.raises(MasterError) as exc:
        masters.set_specialties(db, master, [ids[0], 10**9], [])
    assert _code(exc) == "unknown"
    inactive = db.get(Specialty, ids[0])
    inactive.is_active = False
    db.flush()
    with pytest.raises(MasterError) as exc:
        masters.set_specialties(db, master, [inactive.id], [])
    assert _code(exc) == "unknown"
    masters.set_specialties(db, master, ids[1:4], ids[1:2])
    masters.set_specialties(db, master, ids[2:4], [])  # replaces, not appends
    view = masters.profile_view(db, master, lang="ru", viewer=None)
    assert len(view.specialties) == 2


def test_areas_district_must_belong_to_city(db: Session) -> None:
    _, master = _master(db)
    dushanbe = _city(db)
    sino = db.scalars(select(District).where(District.city_id == dushanbe.id)).first()
    other = db.scalars(select(City).where(City.id != dushanbe.id)).first()
    masters.set_areas(db, master, [other.id], [sino.id])
    view = masters.profile_view(db, master, lang="ru", viewer=None)
    assert len(view.areas) == 2
    assert any(sino.name_ru in a and dushanbe.name_ru in a for a in view.areas)
    # a district id that does not exist
    with pytest.raises(MasterError) as exc:
        masters.set_areas(db, master, [], [10**9])
    assert _code(exc) == "area"
    with pytest.raises(MasterError) as exc:
        masters.set_areas(db, master, [10**9], [])
    assert _code(exc) == "area"
    sino.is_active = False
    db.flush()
    with pytest.raises(MasterError) as exc:
        masters.set_areas(db, master, [], [sino.id])
    assert _code(exc) == "area"
    sino.is_active = True
    db.flush()
    cities = list(db.scalars(select(City.id).where(City.is_active).limit(21)))
    with pytest.raises(MasterError) as exc:
        masters.set_areas(db, master, cities, [])
    assert _code(exc) == "limit"


def test_contacts_validation(db: Session) -> None:
    _, master = _master(db)
    masters.set_contacts(db, master, telegram="@my_name", whatsapp=None, instagram=None)
    assert master.contact_telegram == "my_name"
    with pytest.raises(MasterError) as exc:
        masters.set_contacts(db, master, telegram="ab", whatsapp=None, instagram=None)
    assert _code(exc) == "telegram"
    masters.set_contacts(
        db, master, telegram=None, whatsapp="992 90 123 45 67", instagram="@shop.tj"
    )
    assert master.contact_whatsapp == "+992901234567"
    assert master.contact_instagram == "shop.tj" and master.contact_telegram is None
    with pytest.raises(MasterError) as exc:
        masters.set_contacts(db, master, telegram=None, whatsapp="abc", instagram=None)
    assert _code(exc) == "whatsapp"
    with pytest.raises(MasterError) as exc:
        masters.set_contacts(db, master, telegram=None, whatsapp=None, instagram="bad handle!")
    assert _code(exc) == "instagram"
    masters.set_contacts(db, master, telegram="", whatsapp=" ", instagram="")
    assert master.contact_whatsapp is None and master.contact_instagram is None


def test_publish_needs_phone_specialty_area(db: Session) -> None:
    user, master = _master(db, verified=False)
    assert masters.missing_for_publish(db, master) == ["phone", "specialty", "area"]
    assert masters.refresh_publication(db, master) is False
    masters.set_specialties(db, master, _specialties(db, 1), [])
    assert masters.missing_for_publish(db, master) == ["phone", "area"]
    masters.set_areas(db, master, [_city(db).id], [])
    assert masters.missing_for_publish(db, master) == ["phone"]
    assert master.published_at is None and master.contact_phone is None
    user.phone = "+992900000001"
    user.phone_verified_at = utcnow()
    db.flush()
    assert masters.missing_for_publish(db, master) == []
    assert masters.refresh_publication(db, master) is True
    assert master.published_at is not None and master.contact_phone == "+992900000001"
    first = master.published_at
    masters.refresh_publication(db, master)
    assert master.published_at == first  # original timestamp kept
    master.hidden_at = utcnow()
    assert masters.missing_for_publish(db, master) == ["hidden"]
    assert masters.refresh_publication(db, master) is False
    assert master.published_at is None


def test_unpublished_when_last_specialty_removed(db: Session) -> None:
    _, master = _ready(db)
    assert master.published_at is not None
    masters.set_specialties(db, master, [], [])
    assert master.published_at is None
    assert masters.public_master(db, master.slug, None) is None
    masters.set_specialties(db, master, _specialties(db, 1), [])
    assert master.published_at is not None
    masters.set_areas(db, master, [], [])
    assert master.published_at is None
    assert masters.public_master(db, master.slug, None) is None


def test_inactive_owner_is_listed_as_missing(db: Session) -> None:
    user, master = _ready(db)
    user.blocked_at = utcnow()
    assert masters.missing_for_publish(db, master) == ["inactive"]


def test_blocked_owner_profile_is_not_public(db: Session) -> None:
    user, master = _ready(db)
    assert masters.public_master(db, master.slug, None) is master
    user.blocked_at = utcnow()
    db.flush()
    assert masters.public_master(db, master.slug, None) is None
    other = _user(db)
    assert masters.public_master(db, master.slug, other) is None


def test_hidden_profile_is_not_public(db: Session) -> None:
    _, master = _ready(db)
    master.hidden_at = utcnow()
    db.flush()
    assert masters.public_master(db, master.slug, None) is None


def test_draft_visible_only_to_owner(db: Session) -> None:
    user, master = _master(db)
    assert masters.public_master(db, master.slug, None) is None
    assert masters.public_master(db, master.slug, _user(db)) is None
    assert masters.public_master(db, master.slug, user) is master
    view = masters.profile_view(db, master, lang="ru", viewer=user)
    assert view.is_draft is True


def test_contacts_none_for_guest_view(db: Session) -> None:
    user, master = _ready(db)
    masters.set_contacts(db, master, telegram="my_name", whatsapp=None, instagram=None)
    guest = masters.profile_view(db, master, lang="ru", viewer=None)
    assert guest.contacts is None and "my_name" not in guest.model_dump_json()
    assert master.contact_phone not in guest.model_dump_json()
    blocked = _user(db, blocked=True)
    assert masters.profile_view(db, master, lang="ru", viewer=blocked).contacts is None
    signed_in = masters.profile_view(db, master, lang="ru", viewer=_user(db))
    assert signed_in.contacts is not None
    assert signed_in.contacts.telegram == "my_name"
    assert signed_in.contacts.phone == master.contact_phone
    assert masters.profile_view(db, master, lang="ru", viewer=user).contacts is not None


def test_profile_view_language_order_and_avatar(db: Session) -> None:
    _, master = _ready(db)
    ids = _specialties(db, 3)
    masters.set_specialties(db, master, ids, [ids[2]])
    masters.set_avatar(db, master, "a/" + "0" * 32 + ".webp")
    ru = masters.profile_view(db, master, lang="ru", viewer=None)
    tg = masters.profile_view(db, master, lang="tg", viewer=None)
    assert ru.specialties[0].primary is True and len(ru.specialties) == 3
    assert ru.specialties[0].name != tg.specialties[0].name
    assert ru.avatar_url and ru.avatar_url.endswith("/" + "0" * 32 + ".webp")
    assert ru.since == master.created_at.date() and ru.collections == []
    assert masters.profile_view(db, master, lang="xx", viewer=None).specialties == ru.specialties


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"display_name": "   "}, "name"),
        ({"display_name": "x" * 81}, "name"),
        ({"headline": "h" * 81}, "headline"),
        ({"experience_years": 61}, "experience"),
        ({"experience_years": -1}, "experience"),
    ],
)
def test_basics_guard_in_service(db: Session, changes: dict, code: str) -> None:
    data = _basics()
    for key, value in changes.items():
        setattr(data, key, value)
    with pytest.raises(MasterError) as exc:
        masters.create_profile(db, _user(db), data)
    assert _code(exc) == code
    _, master = _master(db)
    with pytest.raises(MasterError) as exc:
        masters.update_basics(db, master, data, bio=None)
    assert _code(exc) == code


def test_bio_guard_in_service(db: Session) -> None:
    _, master = _master(db)
    with pytest.raises(MasterError) as exc:
        masters.update_basics(db, master, _basics(), bio="b" * 1001)
    assert _code(exc) == "bio"
    masters.update_basics(db, master, _basics(), bio="b" * 1000)


def test_update_basics_reraises_non_slug_integrity_errors(db: Session, monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    _, master = _master(db)

    def boom(*args, **kwargs):
        raise IntegrityError("stmt", {}, Exception("some other constraint"))

    monkeypatch.setattr(masters, "_apply_basics", boom)
    with pytest.raises(IntegrityError):
        masters.update_basics(db, master, _basics(), bio=None)
