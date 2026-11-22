import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import City, District, MasterArea, MasterProfile, MasterSpecialty, Specialty, User
from tests.support import CSRF, SESSION, csrf_token, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

BASICS = {"display_name": "Али Рахимов", "slug": "ali-rahimov", "headline": "Плиточник"}


def _verify_phone(user_id: int, phone: str = "+992901112233") -> None:
    with get_sessionmaker()() as db:
        user = db.get(User, user_id)
        user.phone, user.phone_verified_at = phone, utcnow()
        db.commit()


def _signed_in(client: TestClient, email: str = "ana@example.com", **kw) -> int:  # type: ignore[no-untyped-def]
    uid = make_user(email, **kw)
    assert sign_in(client, email).status_code == 303
    return uid


def _master(user_id: int) -> MasterProfile | None:
    with get_sessionmaker()() as db:
        return db.scalar(select(MasterProfile).where(MasterProfile.user_id == user_id))


def _ids() -> tuple[int, int]:
    with get_sessionmaker()() as db:
        spec = db.scalars(select(Specialty.id).where(Specialty.is_active)).first()
        city = db.scalars(select(City.id).where(City.slug == "dushanbe")).one()
    return spec, city


def _ready(client: TestClient) -> None:
    spec, city = _ids()
    assert post(client, "/master/onboarding/basics", BASICS).status_code == 303
    assert (
        post(client, "/master/onboarding/specialties", {"specialty": str(spec)}).status_code == 303
    )
    assert post(client, "/master/onboarding/area", {"city": str(city)}).status_code == 303


def _png(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _upload(client: TestClient, data: bytes, name: str = "a.png", next_: str = ""):  # type: ignore[no-untyped-def]
    url = "/master/avatar" + (f"?next={next_}" if next_ else "")
    return client.post(
        url,
        data={"csrf_token": csrf_token(client)},
        files={"upload": (name, data, "application/octet-stream")},
    )


@pytest.mark.parametrize(
    "path",
    [
        "/master/onboarding/basics",
        "/master/onboarding/specialties",
        "/master/onboarding/area",
        "/master/onboarding/phone",
        "/master/edit",
        "/master/slug-check?slug=abc",
    ],
)
def test_guest_is_sent_to_signin(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/signin?next=/master/")


def test_guest_posts_are_sent_to_signin(client: TestClient) -> None:
    for path in ("basics", "specialties", "area", "phone", "phone/check"):
        r = post(client, f"/master/onboarding/{path}", {"display_name": "x"})
        assert r.status_code == 303 and r.headers["location"].startswith("/signin")


def test_basics_creates_profile_and_rotates_session(client: TestClient) -> None:
    uid = _signed_in(client)
    old_session, old_csrf = client.cookies.get(SESSION), client.cookies.get(CSRF)
    assert client.get("/master/onboarding/basics").status_code == 200
    r = post(client, "/master/onboarding/basics", {**BASICS, "is_available": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/master/onboarding/specialties"
    assert client.cookies.get(SESSION) not in (None, old_session)
    assert client.cookies.get(CSRF) not in (None, old_csrf)
    master = _master(uid)
    assert master is not None and master.slug == "ali-rahimov" and master.published_at is None
    # The old session is gone, the new one works.
    assert client.get("/master/edit").status_code == 200


def test_second_submit_edits_and_does_not_rotate_or_duplicate(client: TestClient) -> None:
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    session = client.cookies.get(SESSION)
    page = client.get("/master/onboarding/basics")
    assert "ali-rahimov" in page.text
    r = post(client, "/master/onboarding/basics", {**BASICS, "headline": "Мастер"})
    assert r.status_code == 303
    assert client.cookies.get(SESSION) == session
    with get_sessionmaker()() as db:
        rows = db.scalars(select(MasterProfile).where(MasterProfile.user_id == uid)).all()
    assert len(rows) == 1 and rows[0].headline == "Мастер"


def test_basics_validation_errors(client: TestClient) -> None:
    uid = _signed_in(client)
    r = post(client, "/master/onboarding/basics", {**BASICS, "display_name": " "})
    assert r.status_code == 422 and "Укажите имя" in r.text
    r = post(client, "/master/onboarding/basics", {**BASICS, "experience_years": "99"})
    assert r.status_code == 422
    r = post(client, "/master/onboarding/basics", {**BASICS, "slug": "A B"})
    assert r.status_code == 422
    assert _master(uid) is None


def test_slug_taken_is_409_with_message(client: TestClient) -> None:
    other = TestClient(client.app, follow_redirects=False)
    _signed_in(other, "b@example.com")
    post(other, "/master/onboarding/basics", BASICS)
    uid = _signed_in(client)
    r = post(client, "/master/onboarding/basics", BASICS)
    assert r.status_code == 409 and "уже занят" in r.text
    assert _master(uid) is None


def test_steps_refuse_user_without_profile(client: TestClient) -> None:
    _signed_in(client)
    for path in ("specialties", "area", "phone"):
        r = client.get(f"/master/onboarding/{path}")
        assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"
        r = post(client, f"/master/onboarding/{path}")
        assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"
    r = client.get("/master/edit")
    assert r.headers["location"] == "/master/onboarding/basics"
    assert post(client, "/master/onboarding/phone/check").headers["location"].endswith("basics")
    r = _upload(client, _png())
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"


def test_slug_check_fragment(client: TestClient) -> None:
    _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    assert "свободен" in client.get("/master/slug-check?slug=free-slug").text
    assert "свободен" in client.get("/master/slug-check?slug=ali-rahimov").text  # my own
    assert "ali-rahimov" not in client.get("/master/slug-check?slug=ab").text
    assert "от 3 до 40" in client.get("/master/slug-check?slug=ab").text
    other = TestClient(client.app, follow_redirects=False)
    _signed_in(other, "b@example.com")
    taken = other.get("/master/slug-check?slug=ali-rahimov").text
    assert "занят" in taken and "ali-rahimov-2" in taken


def test_specialties_and_area_forms_save(client: TestClient) -> None:
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    page = client.get("/master/onboarding/specialties")
    assert 'name="specialty"' in page.text and 'name="primary"' in page.text
    with get_sessionmaker()() as db:
        ids = list(db.scalars(select(Specialty.id).where(Specialty.is_active).limit(3)))
        city = db.scalars(select(City).where(City.slug == "dushanbe")).one()
        district = db.scalars(select(District).where(District.city_id == city.id)).first()
    r = post(
        client,
        "/master/onboarding/specialties",
        {"specialty": [str(i) for i in ids], "primary": str(ids[0])},
    )
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/area"
    area = client.get("/master/onboarding/area").text
    assert 'name="district"' in area
    r = post(client, "/master/onboarding/area", {"district": str(district.id)})
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/phone"
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile).where(MasterProfile.user_id == uid)).one()
        specs = db.execute(
            select(MasterSpecialty.specialty_id, MasterSpecialty.is_primary).where(
                MasterSpecialty.master_id == master.id
            )
        ).all()
        areas = db.scalars(select(MasterArea).where(MasterArea.master_id == master.id)).all()
    assert {s for s, _ in specs} == set(ids) and [s for s, p in specs if p] == [ids[0]]
    assert [(a.city_id, a.district_id) for a in areas] == [(city.id, district.id)]
    # Saved choices are ticked when the form is opened again.
    assert "checked" in client.get("/master/onboarding/specialties").text


def test_step_errors_are_friendly(client: TestClient) -> None:
    _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    r = post(client, "/master/onboarding/specialties", {"specialty": "abc"})
    assert r.status_code == 422 and "неизвестный" in r.text
    r = post(client, "/master/onboarding/specialties", {"specialty": "999999"})
    assert r.status_code == 422
    r = post(client, "/master/onboarding/area", {})
    assert r.status_code == 422 and "хотя бы один" in r.text
    r = post(client, "/master/onboarding/phone", {"telegram": "no"})
    assert r.status_code == 422 and "Telegram" in r.text


def test_inactive_specialty_is_not_listed_or_ticked(client: TestClient) -> None:
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    spec, _ = _ids()
    post(client, "/master/onboarding/specialties", {"specialty": str(spec)})
    with get_sessionmaker()() as db:
        row = db.get(Specialty, spec)
        row.is_active = False
        db.commit()
    try:
        page = client.get("/master/onboarding/specialties").text
        assert f'value="{spec}"' not in page
    finally:
        with get_sessionmaker()() as db:
            db.get(Specialty, spec).is_active = True
            db.commit()
    assert _master(uid) is not None


def test_phone_step_finishes_and_publishes_with_verified_phone(client: TestClient) -> None:
    uid = _signed_in(client)
    _verify_phone(uid)
    _ready(client)
    assert "+992901112233" in client.get("/master/onboarding/phone").text
    r = post(client, "/master/onboarding/phone", {"telegram": "@ali_master"})
    assert r.status_code == 303
    assert r.headers["location"] == "/m/ali-rahimov?notice=published"
    master = _master(uid)
    assert master.published_at is not None and master.contact_telegram == "ali_master"
    assert master.contact_phone == "+992901112233"


def test_phone_step_shows_missing_without_phone(client: TestClient) -> None:
    uid = _signed_in(client)
    _ready(client)
    assert "подтвердите телефон" in client.get("/master/onboarding/phone").text
    r = post(client, "/master/onboarding/phone", {})
    assert r.headers["location"] == "/m/ali-rahimov?notice=incomplete"
    assert _master(uid).published_at is None
    assert "телефона" in client.get("/master/edit").text


def test_check_again_and_finish_publish_after_late_verification(client: TestClient) -> None:
    uid = _signed_in(client)
    _ready(client)
    assert _master(uid).published_at is None
    _verify_phone(uid)  # verified in Telegram while the form was open
    r = post(client, "/master/onboarding/phone/check")
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/phone"
    assert _master(uid).published_at is not None


def test_finish_publishes_after_late_verification(client: TestClient) -> None:
    uid = _signed_in(client)
    _ready(client)
    _verify_phone(uid)
    r = post(client, "/master/onboarding/phone", {})
    assert r.headers["location"].endswith("notice=published")
    assert _master(uid).published_at is not None


def test_edit_page_sections_and_next_edit(client: TestClient) -> None:
    uid = _signed_in(client)
    _ready(client)
    page = client.get("/master/edit")
    assert page.status_code == 200
    assert "?next=edit" in page.text and 'name="bio"' in page.text
    r = post(client, "/master/onboarding/basics?next=edit", {**BASICS, "bio": "Про меня"})
    assert r.status_code == 303 and r.headers["location"] == "/master/edit?saved=1"
    assert _master(uid).bio == "Про меня"
    assert "Сохранено" in client.get("/master/edit?saved=1").text
    r = post(client, "/master/onboarding/area?next=edit", {})
    assert r.status_code == 422 and 'name="bio"' in r.text  # error shown on the edit page
    r = post(client, "/master/onboarding/phone?next=edit", {"instagram": "ali.master"})
    assert r.headers["location"] == "/master/edit?saved=1"


def test_account_page_entry_button(client: TestClient) -> None:
    _signed_in(client)
    assert "/master/onboarding/basics" in client.get("/account").text
    post(client, "/master/onboarding/basics", BASICS)
    page = client.get("/account").text
    assert "/master/edit" in page and "/m/ali-rahimov" in page


# --- avatar ---------------------------------------------------------------------------------


def test_avatar_upload_stores_webp_and_replaces_old(client: TestClient) -> None:
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    assert 'name="upload"' in client.get("/master/onboarding/basics").text
    r = _upload(client, _png())
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"
    first = _master(uid).avatar_key
    root = Path(get_settings().media_root)
    assert first.startswith("a/") and (root / first).is_file()
    with Image.open(root / first) as img:
        assert img.format == "WEBP" and img.size == (400, 400)
    assert f"/media/{first}" in client.get("/master/onboarding/basics").text
    r = _upload(client, _png((90, 60)), next_="edit")
    assert r.headers["location"] == "/master/edit?saved=1"
    second = _master(uid).avatar_key
    assert second != first
    assert (root / second).is_file() and not (root / first).exists()


def test_avatar_upload_404_when_uploads_disabled(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    assert _upload(client, _png()).status_code == 404
    assert 'name="upload"' not in client.get("/master/onboarding/basics").text
    assert 'name="upload"' not in client.get("/master/edit").text
    assert _master(uid).avatar_key is None


@pytest.mark.parametrize(
    ("data", "text"),
    [
        (b"GIF89a" + b"0" * 100, "JPEG, PNG или WebP"),
        (b"\x89PNG\r\n\x1a\n" + b"junk", "Не удалось прочитать"),
        (b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024 + 10), "больше 10 МБ"),
    ],
)
def test_avatar_bad_file_shows_friendly_error(client: TestClient, data: bytes, text: str) -> None:
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    r = _upload(client, data)
    assert r.status_code == 422 and text in r.text
    assert _master(uid).avatar_key is None
    assert not list(Path(get_settings().media_root).rglob("*.webp"))


def test_avatar_missing_file_is_friendly(client: TestClient) -> None:
    _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)
    r = client.post(
        "/master/avatar", data={"csrf_token": csrf_token(client)}, files={"x": ("x", b"1")}
    )
    assert r.status_code == 422


def test_avatar_new_file_removed_when_db_fails(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    uid = _signed_in(client)
    post(client, "/master/onboarding/basics", BASICS)

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("db down")

    monkeypatch.setattr("app.web.master.masters.set_avatar", boom)
    failing = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    failing.cookies.update(client.cookies)
    r = _upload(failing, _png())
    assert r.status_code == 500
    assert not list(Path(get_settings().media_root).rglob("*.webp"))
    assert _master(uid).avatar_key is None


def test_ru_error_keys_exist_in_both_locales() -> None:
    import json
    from typing import get_args

    from app.services.masters import MasterCode

    base = Path(__file__).resolve().parent.parent / "app" / "locales"
    keys = [f"master.err.{c}" for c in get_args(MasterCode)]
    keys += [f"master.avatar_err.{c}" for c in ("type", "size", "pixels", "unreadable")]
    keys += [f"master.missing_{m}" for m in ("phone", "specialty", "area", "hidden", "inactive")]
    for lang in ("ru", "tg"):
        catalog = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert [k for k in keys if k not in catalog] == []


# --- someone else tries -----------------------------------------------------------------------


def test_user_b_cannot_change_user_a(client: TestClient) -> None:
    a_id = _signed_in(client)
    _verify_phone(a_id)
    _ready(client)
    post(client, "/master/onboarding/phone", {"telegram": "ali_master"})
    _upload(client, _png())
    before = _master(a_id)

    b = TestClient(client.app, follow_redirects=False)
    b_id = _signed_in(b, "b@example.com")
    spec, city = _ids()
    # B has no profile: every route sends B to step 1, and nothing of A's changes.
    for path, data in [
        ("specialties", {"specialty": str(spec)}),
        ("area", {"city": str(city)}),
        ("phone", {"telegram": "hacker_bob"}),
        ("phone/check", {}),
    ]:
        assert post(b, f"/master/onboarding/{path}", data).headers["location"].endswith("basics")
    assert _upload(b, _png()).headers["location"].endswith("basics")
    # B creates their own profile and edits it, still never A's.
    post(b, "/master/onboarding/basics", {**BASICS, "slug": "bob-master", "display_name": "Боб"})
    post(
        b,
        "/master/onboarding/basics?next=edit",
        {**BASICS, "slug": "bob-master", "display_name": "Боб"},
    )
    post(b, "/master/onboarding/phone", {"telegram": "hacker_bob"})
    _upload(b, _png())
    after = _master(a_id)
    assert (after.slug, after.display_name, after.contact_telegram, after.avatar_key) == (
        before.slug, before.display_name, before.contact_telegram, before.avatar_key,
    )  # fmt: skip
    assert _master(b_id).slug == "bob-master"
    # B cannot take A's slug by editing.
    r = post(b, "/master/onboarding/basics?next=edit", BASICS)
    assert r.status_code == 409
    assert _master(b_id).slug == "bob-master"
