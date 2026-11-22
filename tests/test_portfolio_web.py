import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.body_limit import (
    MAX_FILES_PER_UPLOAD,
    MULTI_UPLOAD_REQUEST_CAP,
    UPLOAD_REQUEST_CAP,
)
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import City, MasterProfile, PortfolioCollection, PortfolioPhoto, Specialty, User
from app.services import portfolio
from app.services.storage import get_storage
from tests.support import make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

BASICS = {"display_name": "Али Рахимов", "slug": "ali-rahimov", "headline": "Плиточник"}
HX = {"HX-Request": "true"}


# --- helpers ----------------------------------------------------------------------------------


def _png(size: tuple[int, int] = (64, 48), color=(200, 30, 30)) -> bytes:  # type: ignore[no-untyped-def]
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _signed_in(client: TestClient, email: str = "ana@example.com") -> int:
    uid = make_user(email)
    assert sign_in(client, email).status_code == 303
    return uid


def _other(client: TestClient, email: str) -> TestClient:
    other = TestClient(client.app, follow_redirects=False)
    _signed_in(other, email)
    return other


def _make_master(client: TestClient, email: str = "ana@example.com", slug: str = "ali-rahimov"):  # type: ignore[no-untyped-def]
    """Sign in and create a ready-to-publish master through the wizard routes."""
    uid = _signed_in(client, email)
    with get_sessionmaker()() as db:
        user = db.get(User, uid)
        user.phone, user.phone_verified_at = f"+9929011{uid:05d}"[:13], utcnow()
        db.commit()
        spec = db.scalars(select(Specialty.id).where(Specialty.is_active)).first()
        city = db.scalars(select(City.id).where(City.slug == "dushanbe")).one()
    assert post(client, "/master/onboarding/basics", {**BASICS, "slug": slug}).status_code == 303
    assert (
        post(client, "/master/onboarding/specialties", {"specialty": str(spec)}).status_code == 303
    )
    assert post(client, "/master/onboarding/area", {"city": str(city)}).status_code == 303
    return uid


def _master_row(uid: int) -> MasterProfile:
    with get_sessionmaker()() as db:
        return db.scalars(select(MasterProfile).where(MasterProfile.user_id == uid)).one()


def _collections(uid: int) -> list[PortfolioCollection]:
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile).where(MasterProfile.user_id == uid)).one()
        return list(
            db.scalars(
                select(PortfolioCollection)
                .where(PortfolioCollection.master_id == master.id)
                .order_by(PortfolioCollection.position)
            )
        )


def _photos(collection_id: int, *, live_only: bool = True) -> list[PortfolioPhoto]:
    with get_sessionmaker()() as db:
        stmt = select(PortfolioPhoto).where(PortfolioPhoto.collection_id == collection_id)
        if live_only:
            stmt = stmt.where(PortfolioPhoto.deleted_at.is_(None))
        return list(db.scalars(stmt.order_by(PortfolioPhoto.position)))


def _new_collection(client: TestClient, title: str = "Кухня") -> int:
    r = post(client, "/master/portfolio/collections", {"title": title, "description": "Плитка"})
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rstrip("/").rsplit("/", 1)[1])


def _upload(client: TestClient, collection_id: int, files: list[tuple[str, bytes]], **kw):  # type: ignore[no-untyped-def]
    return client.post(
        f"/master/portfolio/collections/{collection_id}/photos",
        data={"csrf_token": client.cookies.get("mc_csrf")},
        files=[("photos", (name, data, "application/octet-stream")) for name, data in files],
        **kw,
    )


def _with_photos(client: TestClient, count: int = 2) -> tuple[int, list[int]]:
    cid = _new_collection(client)
    r = _upload(client, cid, [(f"{i}.png", _png((64 + i, 48))) for i in range(count)])
    assert r.status_code == 303, r.text
    return cid, [p.id for p in _photos(cid)]


# --- guests -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/master/portfolio",
        "/master/portfolio/collections/1",
        "/master/onboarding/collection",
    ],
)
def test_guest_is_sent_to_signin(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 303 and r.headers["location"] == f"/signin?next={path}"


def test_guest_post_without_csrf_is_403_and_with_csrf_goes_to_signin(client: TestClient) -> None:
    assert client.post("/master/portfolio/collections", data={"title": "x"}).status_code == 403
    r = post(client, "/master/portfolio/collections", {"title": "x"})
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")


def test_user_without_profile_is_sent_to_basics(client: TestClient) -> None:
    _signed_in(client)
    r = client.get("/master/portfolio")
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"
    r = post(client, "/master/portfolio/collections", {"title": "x"})
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"


# --- collections ------------------------------------------------------------------------------


def test_portfolio_page_lists_and_creates_collections(client: TestClient) -> None:
    uid = _make_master(client)
    page = client.get("/master/portfolio")
    assert page.status_code == 200 and 'name="title"' in page.text
    cid = _new_collection(client, "Кухня")
    assert [c.title for c in _collections(uid)] == ["Кухня"]
    page = client.get("/master/portfolio")
    assert "Кухня" in page.text and f"/master/portfolio/collections/{cid}" in page.text


def test_create_collection_validation_and_limit(client: TestClient) -> None:
    uid = _make_master(client)
    r = post(client, "/master/portfolio/collections", {"title": "   "})
    assert r.status_code == 422 and "Укажите название" in r.text
    r = post(client, "/master/portfolio/collections", {"title": "x" * 121})
    assert r.status_code == 422
    assert _collections(uid) == []
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile)).one()
        for i in range(get_settings().limit("collections")):
            portfolio.create_collection(db, master, f"c{i}", None)
        db.commit()
    r = post(client, "/master/portfolio/collections", {"title": "one more"})
    assert r.status_code == 422 and "лимит" in r.text.lower()


def test_edit_collection_title_and_description(client: TestClient) -> None:
    uid = _make_master(client)
    cid = _new_collection(client)
    page = client.get(f"/master/portfolio/collections/{cid}")
    assert page.status_code == 200 and "Кухня" in page.text
    r = post(
        client,
        f"/master/portfolio/collections/{cid}",
        {"title": "Ванная", "description": "Мозаика"},
    )
    assert r.status_code == 303
    c = _collections(uid)[0]
    assert (c.title, c.description) == ("Ванная", "Мозаика")
    r = post(client, f"/master/portfolio/collections/{cid}", {"title": ""})
    assert r.status_code == 422 and "Укажите название" in r.text
    assert _collections(uid)[0].title == "Ванная"


def test_hide_and_show_collection(client: TestClient) -> None:
    uid = _make_master(client)
    cid = _new_collection(client)
    r = post(client, f"/master/portfolio/collections/{cid}/hide", {"hidden": "1"})
    assert r.status_code == 303 and _collections(uid)[0].hidden_at is not None
    r = post(client, f"/master/portfolio/collections/{cid}/hide", {"hidden": "0"})
    assert r.status_code == 303 and _collections(uid)[0].hidden_at is None


def test_move_collection_up_and_down(client: TestClient) -> None:
    uid = _make_master(client)
    a, b = _new_collection(client, "A"), _new_collection(client, "B")
    r = post(client, f"/master/portfolio/collections/{b}/move?dir=up")
    assert r.status_code == 303
    assert [c.title for c in _collections(uid)] == ["B", "A"]
    post(client, f"/master/portfolio/collections/{b}/move?dir=down")
    assert [c.title for c in _collections(uid)] == ["A", "B"]
    assert post(client, f"/master/portfolio/collections/{a}/move?dir=sideways").status_code == 422


# --- photos -----------------------------------------------------------------------------------


def test_upload_one_photo_and_show_it(client: TestClient) -> None:
    _make_master(client)
    cid = _new_collection(client)
    r = _upload(client, cid, [("a.png", _png())])
    assert r.status_code == 303
    photos = _photos(cid)
    assert len(photos) == 1
    page = client.get(f"/master/portfolio/collections/{cid}")
    assert f"/media/{photos[0].thumb_key}" in page.text
    assert client.get(f"/media/{photos[0].storage_key}").status_code == 200
    assert "multiple" in page.text and 'enctype="multipart/form-data"' in page.text


def test_multi_upload_reports_bad_files_and_keeps_good_ones(client: TestClient) -> None:
    _make_master(client)
    cid = _new_collection(client)
    files = [
        ("one.png", _png()),
        ("<script>evil.jpg", b"this is not an image at all"),
        ("three.png", _png((80, 60))),
        ("cut.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 10),
    ]
    r = _upload(client, cid, files)
    assert r.status_code == 200
    assert len(_photos(cid)) == 2  # the good ones are saved
    assert "Файл 2" in r.text and "Файл 4" in r.text
    assert "Файл 1:" not in r.text and "Файл 3:" not in r.text
    assert "evil" not in r.text and "<script>" not in r.text  # no client file names in the page


def test_all_files_bad_is_422(client: TestClient) -> None:
    _make_master(client)
    cid = _new_collection(client)
    r = _upload(client, cid, [("a.txt", b"hello")])
    assert r.status_code == 422 and "Файл 1" in r.text
    assert _photos(cid) == []


def test_no_files_is_a_friendly_error(client: TestClient) -> None:
    _make_master(client)
    cid = _new_collection(client)
    r = post(client, f"/master/portfolio/collections/{cid}/photos")
    assert r.status_code == 422 and "Выберите" in r.text


def test_more_than_ten_files_is_refused_with_a_friendly_error(client: TestClient) -> None:
    _make_master(client)
    cid = _new_collection(client)
    r = _upload(
        client, cid, [(f"{i}.png", _png((16, 16))) for i in range(MAX_FILES_PER_UPLOAD + 1)]
    )
    assert r.status_code == 422 and "не больше 10" in r.text
    assert _photos(cid) == []
    r = _upload(client, cid, [(f"{i}.png", _png((16, 16))) for i in range(MAX_FILES_PER_UPLOAD)])
    assert r.status_code == 303 and len(_photos(cid)) == MAX_FILES_PER_UPLOAD


def test_body_over_multi_cap_is_413_and_cap_is_per_route(client: TestClient) -> None:
    assert MULTI_UPLOAD_REQUEST_CAP > UPLOAD_REQUEST_CAP
    big = b"x" * (MULTI_UPLOAD_REQUEST_CAP + 1)
    for path in ("/master/portfolio/collections/1/photos", "/master/onboarding/collection"):
        assert client.post(path, content=big).status_code == 413
    # Between the two caps: the multi-upload routes still read it, the avatar route refuses it.
    mid = b"x" * (UPLOAD_REQUEST_CAP + 1000)
    assert client.post("/master/avatar", content=mid).status_code == 413
    assert client.post("/master/portfolio/collections/1/photos", content=mid).status_code == 403


def test_photo_limit_per_collection_is_reported(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _make_master(client)
    cid = _new_collection(client)
    monkeypatch.setenv(
        "PLAN_LIMITS",
        json.dumps({"free": {**get_settings().plan_limits["free"], "photos_per_collection": 1}}),
    )
    get_settings.cache_clear()
    r = _upload(client, cid, [("a.png", _png()), ("b.png", _png()), ("c.png", _png())])
    assert r.status_code == 200 and len(_photos(cid)) == 1
    assert "Файл 2" in r.text and "Файл 3" in r.text


def test_integrity_error_on_save_is_a_friendly_error(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _make_master(client)
    cid = _new_collection(client)

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr("app.web.portfolio.portfolio.add_photo", boom)
    r = _upload(client, cid, [("a.png", _png())])
    assert r.status_code == 422 and "Файл 1" in r.text
    assert _photos(cid) == []


def test_upload_routes_404_when_uploads_disabled(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    uid = _make_master(client)
    cid = _new_collection(client)
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    r = _upload(client, cid, [("a.png", _png())])
    assert r.status_code == 404
    assert _photos(cid) == []
    page = client.get(f"/master/portfolio/collections/{cid}")
    assert page.status_code == 200 and 'type="file"' not in page.text
    # Everything that is not a file upload keeps working.
    assert _new_collection(client, "Вторая") > cid
    assert (
        post(client, f"/master/portfolio/collections/{cid}", {"title": "Новая"}).status_code == 303
    )
    assert (
        post(client, f"/master/portfolio/collections/{cid}/hide", {"hidden": "1"}).status_code
        == 303
    )
    assert post(client, f"/master/portfolio/collections/{cid}/move?dir=down").status_code == 303
    assert len(_collections(uid)) == 2
    step = client.get("/master/onboarding/collection")
    assert step.status_code == 200 and 'type="file"' not in step.text


def test_move_photo_left_and_right(client: TestClient) -> None:
    _make_master(client)
    cid, (p1, p2) = _with_photos(client)
    r = post(client, f"/master/portfolio/photos/{p2}/move?dir=left")
    assert r.status_code == 303 and r.headers["location"] == f"/master/portfolio/collections/{cid}"
    assert [p.id for p in _photos(cid)] == [p2, p1]
    post(client, f"/master/portfolio/photos/{p2}/move?dir=right")
    assert [p.id for p in _photos(cid)] == [p1, p2]
    assert post(client, f"/master/portfolio/photos/{p2}/move?dir=up").status_code == 422


def test_delete_then_undo_via_htmx(client: TestClient) -> None:
    _make_master(client)
    cid, (p1, p2) = _with_photos(client)
    token = client.cookies.get("mc_csrf")
    r = client.post(f"/master/portfolio/photos/{p1}/delete", headers={**HX, "X-CSRF-Token": token})
    assert r.status_code == 200
    assert f"/master/portfolio/photos/{p1}/undo" in r.text and "Отменить" in r.text
    assert "<html" not in r.text  # a fragment, not a page
    assert [p.id for p in _photos(cid)] == [p2]
    assert f"photos/{p1}/" not in client.get(f"/master/portfolio/collections/{cid}").text
    r = client.post(f"/master/portfolio/photos/{p1}/undo", headers={**HX, "X-CSRF-Token": token})
    assert r.status_code == 200 and "<html" not in r.text
    assert f"/master/portfolio/photos/{p1}/delete" in r.text  # the tile is back
    assert [p.id for p in _photos(cid)] == [p1, p2]


def test_delete_and_undo_without_htmx_redirect(client: TestClient) -> None:
    _make_master(client)
    cid, (p1, _p2) = _with_photos(client)
    r = post(client, f"/master/portfolio/photos/{p1}/delete")
    assert r.status_code == 303
    assert r.headers["location"] == f"/master/portfolio/collections/{cid}?undo={p1}"
    page = client.get(r.headers["location"])
    assert f"/master/portfolio/photos/{p1}/undo" in page.text
    r = post(client, f"/master/portfolio/photos/{p1}/undo")
    assert r.status_code == 303 and r.headers["location"] == f"/master/portfolio/collections/{cid}"
    assert len(_photos(cid)) == 2


def test_undo_of_a_live_photo_is_404(client: TestClient) -> None:
    _make_master(client)
    _cid, (p1, _p2) = _with_photos(client)
    assert post(client, f"/master/portfolio/photos/{p1}/undo").status_code == 404


def test_undo_when_expired_or_full_is_a_friendly_message(client: TestClient) -> None:
    _make_master(client)
    cid, (p1, _p2) = _with_photos(client)
    post(client, f"/master/portfolio/photos/{p1}/delete")
    with get_sessionmaker()() as db:
        photo = db.get(PortfolioPhoto, p1)
        photo.deleted_at = utcnow() - portfolio.UNDO_WINDOW - portfolio.UNDO_WINDOW
        db.commit()
    token = client.cookies.get("mc_csrf")
    r = client.post(f"/master/portfolio/photos/{p1}/undo", headers={**HX, "X-CSRF-Token": token})
    assert r.status_code == 200 and "истёк" in r.text
    r = post(client, f"/master/portfolio/photos/{p1}/undo")
    assert r.status_code == 303 and "error=undo_expired" in r.headers["location"]
    page = client.get(r.headers["location"])
    assert "истёк" in page.text
    assert cid


# --- someone else tries -----------------------------------------------------------------------


def test_user_b_cannot_touch_user_a_portfolio(client: TestClient) -> None:
    a_uid = _make_master(client)
    cid, (p1, _p2) = _with_photos(client)
    before_photos = [(p.id, p.position, p.deleted_at) for p in _photos(cid, live_only=False)]
    before_col = [(c.title, c.description, c.hidden_at, c.position) for c in _collections(a_uid)]

    b = _other(client, "bo@example.com")
    # B has no profile of their own: sent to the wizard, nothing happens.
    for path, data in [
        (f"/master/portfolio/collections/{cid}", {"title": "hacked"}),
        (f"/master/portfolio/collections/{cid}/hide", {"hidden": "1"}),
        (f"/master/portfolio/collections/{cid}/move?dir=down", {}),
        (f"/master/portfolio/photos/{p1}/move?dir=right", {}),
        (f"/master/portfolio/photos/{p1}/delete", {}),
        (f"/master/portfolio/photos/{p1}/undo", {}),
    ]:
        r = post(b, path, data)
        assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics", path
    assert _upload(b, cid, [("a.png", _png())]).status_code == 303
    assert b.get(f"/master/portfolio/collections/{cid}").status_code == 303

    # Now B has a profile: A's ids are "not found".
    _make_master_for_existing(b)
    assert b.get(f"/master/portfolio/collections/{cid}").status_code == 404
    for path, data in [
        (f"/master/portfolio/collections/{cid}", {"title": "hacked"}),
        (f"/master/portfolio/collections/{cid}/hide", {"hidden": "1"}),
        (f"/master/portfolio/collections/{cid}/move?dir=down", {}),
        (f"/master/portfolio/photos/{p1}/move?dir=right", {}),
        (f"/master/portfolio/photos/{p1}/delete", {}),
        (f"/master/portfolio/photos/{p1}/undo", {}),
    ]:
        assert post(b, path, data).status_code == 404, path
    r = _upload(b, cid, [("a.png", _png())])
    assert r.status_code == 404

    assert [
        (p.id, p.position, p.deleted_at) for p in _photos(cid, live_only=False)
    ] == before_photos
    assert [
        (c.title, c.description, c.hidden_at, c.position) for c in _collections(a_uid)
    ] == before_col
    assert len(list(Path(get_settings().media_root).rglob("*.webp"))) == 4  # 2 photos x 2 files


def _make_master_for_existing(client: TestClient) -> None:
    with get_sessionmaker()() as db:
        spec = db.scalars(select(Specialty.id).where(Specialty.is_active)).first()
        city = db.scalars(select(City.id).where(City.slug == "dushanbe")).one()
    assert (
        post(client, "/master/onboarding/basics", {**BASICS, "slug": "bo-master"}).status_code
        == 303
    )
    post(client, "/master/onboarding/specialties", {"specialty": str(spec)})
    post(client, "/master/onboarding/area", {"city": str(city)})


def test_user_b_cannot_undo_or_see_a_deleted_photo_of_a(client: TestClient) -> None:
    _make_master(client)
    cid, (p1, _p2) = _with_photos(client)
    post(client, f"/master/portfolio/photos/{p1}/delete")
    b = _other(client, "bo@example.com")
    _make_master_for_existing(b)
    assert post(b, f"/master/portfolio/photos/{p1}/undo").status_code == 404
    assert len(_photos(cid)) == 1


# --- onboarding step 5 ------------------------------------------------------------------------


def test_step_four_goes_on_to_step_five_and_wizard_shows_five_steps(client: TestClient) -> None:
    _make_master(client)
    r = post(client, "/master/onboarding/phone", {"telegram": "@ali_master"})
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/collection"
    page = client.get("/master/onboarding/collection")
    assert page.status_code == 200
    assert "5. Первая коллекция" in page.text and "1. Основное" in page.text
    assert 'name="photos"' in page.text and 'name="skip"' in page.text


def test_step_five_skip_publishes(client: TestClient) -> None:
    uid = _make_master(client)
    post(client, "/master/onboarding/phone", {})
    r = post(client, "/master/onboarding/collection", {"skip": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/m/ali-rahimov?notice=published"
    assert _master_row(uid).published_at is not None
    assert _collections(uid) == []


def test_step_five_skip_without_phone_is_incomplete(client: TestClient) -> None:
    uid = _signed_in(client)
    with get_sessionmaker()() as db:
        spec = db.scalars(select(Specialty.id).where(Specialty.is_active)).first()
        city = db.scalars(select(City.id).where(City.slug == "dushanbe")).one()
    post(client, "/master/onboarding/basics", BASICS)
    post(client, "/master/onboarding/specialties", {"specialty": str(spec)})
    post(client, "/master/onboarding/area", {"city": str(city)})
    r = post(client, "/master/onboarding/collection", {"skip": "1"})
    assert r.headers["location"] == "/m/ali-rahimov?notice=incomplete"
    assert _master_row(uid).published_at is None


def test_step_five_create_with_photos_publishes(client: TestClient) -> None:
    uid = _make_master(client)
    r = client.post(
        "/master/onboarding/collection",
        data={"csrf_token": client.cookies.get("mc_csrf"), "title": "Кухня", "description": ""},
        files=[
            ("photos", ("a.png", _png(), "image/png")),
            ("photos", ("b.png", _png(), "image/png")),
        ],
    )
    assert r.status_code == 303 and r.headers["location"] == "/m/ali-rahimov?notice=published"
    (collection,) = _collections(uid)
    assert collection.title == "Кухня" and len(_photos(collection.id)) == 2
    assert _master_row(uid).published_at is not None


def test_step_five_create_without_files_works(client: TestClient) -> None:
    uid = _make_master(client)
    r = post(client, "/master/onboarding/collection", {"title": "Пока пусто"})
    assert r.status_code == 303 and r.headers["location"].startswith("/m/ali-rahimov?notice=")
    assert [c.title for c in _collections(uid)] == ["Пока пусто"]


def test_step_five_blank_title_is_422_and_creates_nothing(client: TestClient) -> None:
    uid = _make_master(client)
    r = post(client, "/master/onboarding/collection", {"title": " "})
    assert r.status_code == 422 and "Укажите название" in r.text
    assert _collections(uid) == []


def test_step_five_bad_file_keeps_the_collection_and_waits_for_finish(client: TestClient) -> None:
    uid = _make_master(client)
    r = client.post(
        "/master/onboarding/collection",
        data={"csrf_token": client.cookies.get("mc_csrf"), "title": "Кухня"},
        files=[
            ("photos", ("a.png", _png(), "image/png")),
            ("photos", ("b.txt", b"nope", "text/plain")),
        ],
    )
    assert r.status_code == 200 and "Файл 2" in r.text
    (collection,) = _collections(uid)
    assert len(_photos(collection.id)) == 1
    assert 'name="skip"' in r.text
    r = post(client, "/master/onboarding/collection", {"skip": "1"})
    assert r.headers["location"] == "/m/ali-rahimov?notice=published"


def test_step_five_too_many_files_creates_nothing(client: TestClient) -> None:
    uid = _make_master(client)
    r = client.post(
        "/master/onboarding/collection",
        data={"csrf_token": client.cookies.get("mc_csrf"), "title": "Кухня"},
        files=[("photos", (f"{i}.png", _png((8, 8)), "image/png")) for i in range(11)],
    )
    assert r.status_code == 422 and "не больше 10" in r.text
    assert _collections(uid) == []


def test_step_five_without_profile_goes_to_basics(client: TestClient) -> None:
    _signed_in(client)
    r = client.get("/master/onboarding/collection")
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"
    r = post(client, "/master/onboarding/collection", {"skip": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/master/onboarding/basics"


def test_owner_pages_do_not_cache_and_media_is_served(client: TestClient) -> None:
    _make_master(client)
    cid, _ids = _with_photos(client, 1)
    r = client.get(f"/master/portfolio/collections/{cid}")
    assert r.headers["cache-control"] == "no-store"
    storage = get_storage()
    assert storage.url(_photos(cid)[0].thumb_key).startswith("/media/")


def test_locale_keys_exist_in_both_languages() -> None:
    from typing import get_args

    from app.services.portfolio import PortfolioCode

    base = Path(__file__).resolve().parent.parent / "app" / "locales"
    keys = [f"portfolio.err.{c}" for c in get_args(PortfolioCode)]
    keys += [
        f"portfolio.file_err.{c}" for c in ("type", "size", "pixels", "unreadable", "limit", "save")
    ]
    keys += ["master.step5", "portfolio.err.too_many", "portfolio.err.no_files"]
    for lang in ("ru", "tg"):
        catalog = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        assert [k for k in keys if k not in catalog] == []
