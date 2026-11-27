import io
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.body_limit import (
    DEFAULT_CAP,
    REQUEST_UPLOAD_REQUEST_CAP,
    UPLOAD_REQUEST_CAP,
    request_cap,
)
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import local_today, utcnow
from app.models import (
    City,
    District,
    JobRequest,
    MasterProfile,
    RequestPhoto,
    Specialty,
)
from app.services.images import MAX_UPLOAD_BYTES
from app.services.storage import get_storage
from tests.support import csrf_token, make_user, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

ADDRESS = "ул. Секретная 42, кв. 7"


# --- helpers ----------------------------------------------------------------------------------


def _png(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _signed_in(client: TestClient, email: str = "ana@example.com") -> int:
    uid = make_user(email)
    assert sign_in(client, email).status_code == 303
    return uid


def _other(client: TestClient, email: str = "boris@example.com") -> TestClient:
    other = TestClient(client.app, follow_redirects=False)
    _signed_in(other, email)
    return other


def _ids() -> dict[str, int]:
    with get_sessionmaker()() as db:
        specs = list(
            db.scalars(select(Specialty.id).where(Specialty.is_active).order_by(Specialty.id))
        )
        plain = db.scalars(
            select(City)
            .where(
                City.is_active, City.id.not_in(select(District.city_id).where(District.is_active))
            )
            .order_by(City.id)
        ).first()
        dushanbe = db.scalars(select(City).where(City.slug == "dushanbe")).one()
        district = db.scalars(
            select(District).where(District.city_id == dushanbe.id, District.is_active)
        ).first()
        return {
            "s1": specs[0],
            "s2": specs[1],
            "s3": specs[2],
            "s4": specs[3],
            "plain": plain.id,
            "dushanbe": dushanbe.id,
            "district": district.id,
        }


def _form(**kw: object) -> dict[str, object]:
    ids = _ids()
    base: dict[str, object] = {
        "specialty": [str(ids["s1"])],
        "city": str(ids["plain"]),
        "district": "",
        "description": "Нужно переложить плитку в ванной комнате",
        "budget_amount": "",
        "budget_unit": "",
        "timing": "flexible",
        "timing_date": "",
        "address": ADDRESS,
    }
    return {**base, **kw}


def _post(client: TestClient, url: str, data: dict[str, object] | None = None, **kw):  # type: ignore[no-untyped-def]
    return client.post(url, data={**(data or {}), "csrf_token": csrf_token(client)}, **kw)


def _create(client: TestClient, **kw: object) -> int:
    r = _post(client, "/requests/new", _form(**kw))
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("?")[0].rstrip("/").rsplit("/", 1)[1])


def _row(request_id: int) -> JobRequest:
    with get_sessionmaker()() as db:
        return db.get(JobRequest, request_id)


def _photos(request_id: int) -> list[RequestPhoto]:
    with get_sessionmaker()() as db:
        return list(
            db.scalars(
                select(RequestPhoto)
                .where(RequestPhoto.request_id == request_id)
                .order_by(RequestPhoto.position)
            )
        )


def _update(request_id: int, **values: object) -> None:
    with get_sessionmaker()() as db:
        row = db.get(JobRequest, request_id)
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def _upload(client: TestClient, request_id: int, files: list[tuple[str, bytes]], **kw):  # type: ignore[no-untyped-def]
    return client.post(
        f"/requests/{request_id}/photos",
        data={"csrf_token": client.cookies.get("mc_csrf")},
        files=[("photos", (name, data, "application/octet-stream")) for name, data in files],
        **kw,
    )


def _make_master(client: TestClient, email: str = "master@example.com") -> int:
    uid = _signed_in(client, email)
    with get_sessionmaker()() as db:
        db.add(MasterProfile(user_id=uid, slug="ali-rahimov", display_name="Али"))
        db.commit()
    return uid


def _stored_keys() -> set[str]:
    root = get_storage().root  # type: ignore[attr-defined]
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# --- guests -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/requests/new", "/requests/mine", "/requests/1"])
def test_guest_is_sent_to_signin(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 303 and r.headers["location"] == f"/signin?next={path}"


@pytest.mark.parametrize(
    "path",
    [
        "/requests/new",
        "/requests/1/edit",
        "/requests/1/photos",
        "/requests/1/photos/1/delete",
        "/requests/1/close",
        "/requests/1/cancel",
        "/requests/1/reopen",
    ],
)
def test_guest_post_needs_csrf_then_signin(client: TestClient, path: str) -> None:
    assert client.post(path, data={}).status_code == 403
    r = _post(client, path)
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")


# --- new request ------------------------------------------------------------------------------


def test_new_page_has_the_form_without_inline_code(client: TestClient) -> None:
    _signed_in(client)
    r = client.get("/requests/new")
    assert r.status_code == 200
    for name in (
        "specialty",
        "city",
        "district",
        "description",
        "budget_amount",
        "timing",
        "address",
    ):
        assert f'name="{name}"' in r.text, name
    assert 'name="csrf_token"' in r.text
    assert 'type="file"' not in r.text  # photos are added on the request's own page
    assert "<script>" not in r.text and "style=" not in r.text
    assert "requests." not in re.sub(r"requests/", "", r.text)  # every key has a text


def test_new_page_prefills_valid_slugs_and_ignores_invalid(client: TestClient) -> None:
    _signed_in(client)
    with get_sessionmaker()() as db:
        city = db.scalars(select(City).where(City.slug == "dushanbe")).one()
        spec = db.get(Specialty, _ids()["s2"])
        spec_slug, city_id, spec_id = spec.slug, city.id, spec.id
    page = client.get(f"/requests/new?c=dushanbe&s={spec_slug}").text
    assert re.search(rf'<option value="{city_id}"[^>]*selected', page)
    assert re.search(rf'value="{spec_id}"[^>]*checked', page)
    bad = client.get("/requests/new?c=nowhere&s=%3Cscript%3E").text
    assert " selected" not in bad.split('name="city"')[1].split("</select>")[0]
    assert not re.search(r'name="specialty"[^>]*checked', bad)
    assert client.get("/requests/new?c=" + "x" * 500 + "&s=").status_code == 200


def test_create_flow_with_prefill_and_listing(client: TestClient) -> None:
    uid = _signed_in(client)
    ids = _ids()
    r = _post(
        client,
        "/requests/new",
        _form(
            specialty=[str(ids["s1"]), str(ids["s2"])],
            budget_amount="150.5",
            budget_unit="m2",
            timing="date",
            timing_date=(local_today() + timedelta(days=5)).isoformat(),
        ),
    )
    assert r.status_code == 303
    rid = int(r.headers["location"].split("?")[0].rsplit("/", 1)[1])
    row = _row(rid)
    assert row.client_id == uid and row.status == "open" and row.address == ADDRESS
    detail = client.get(r.headers["location"])
    assert detail.status_code == 200
    assert "переложить плитку" in detail.text and ADDRESS in detail.text
    assert "requests." not in re.sub(r"requests/", "", detail.text)
    mine = client.get("/requests/mine")
    assert f'href="/requests/{rid}"' in mine.text


def test_create_validation_errors_are_422_with_a_message(client: TestClient) -> None:
    _signed_in(client)
    ids = _ids()
    soon = (local_today() + timedelta(days=2)).isoformat()
    cases: list[dict[str, object]] = [
        {"description": "коротко"},
        {"specialty": []},
        {"specialty": [str(ids[k]) for k in ("s1", "s2", "s3", "s4")]},
        {"city": str(ids["dushanbe"]), "district": ""},  # this city needs a district
        {"city": "999999"},
        {"budget_amount": "10", "budget_unit": ""},
        {"budget_amount": "0", "budget_unit": "m2"},
        {"timing": "date", "timing_date": (local_today() - timedelta(days=1)).isoformat()},
        {"timing": "later"},
        {"address": "x" * 301},
        {"timing": "urgent", "timing_date": soon},
    ]
    for kw in cases:
        r = _post(client, "/requests/new", _form(**kw))
        assert r.status_code == 422, kw
        assert 'role="alert"' in r.text and "requests." not in re.sub(r"requests/", "", r.text), kw
    # The form is shown again with what was typed.
    r = _post(client, "/requests/new", _form(description="коротко", address="моя улица"))
    assert "коротко" in r.text and "моя улица" in r.text
    with get_sessionmaker()() as db:
        assert db.scalars(select(JobRequest)).first() is None


def test_unparseable_input_is_a_friendly_422_never_500(client: TestClient) -> None:
    _signed_in(client)
    safe = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    bad: list[dict[str, object]] = [
        {"timing": "date", "timing_date": "2026-13-45"},
        {"timing": "date", "timing_date": "завтра"},
        {"budget_amount": "много", "budget_unit": "m2"},
        {"budget_amount": "NaN", "budget_unit": "m2"},
        {"budget_amount": "1e999999", "budget_unit": "m2"},
        {"city": "abc"},
        {"city": "9" * 40},
        {"district": "x"},
        {"specialty": ["x"]},
        {"specialty": ["9" * 40]},
    ]
    for kw in bad:
        r = _post(safe, "/requests/new", _form(**kw))
        assert r.status_code == 422, (kw, r.status_code)
        assert 'role="alert"' in r.text, kw
    no_fields = _post(safe, "/requests/new")
    assert no_fields.status_code == 422


def test_district_city_with_districts(client: TestClient) -> None:
    _signed_in(client)
    ids = _ids()
    rid = _create(client, city=str(ids["dushanbe"]), district=str(ids["district"]))
    assert _row(rid).district_id == ids["district"]
    r = _post(client, "/requests/new", _form(city=str(ids["plain"]), district=str(ids["district"])))
    assert r.status_code == 422  # a district of another city


# --- detail and ownership ---------------------------------------------------------------------


def test_only_the_client_opens_the_detail(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    other = _other(client)
    assert other.get(f"/requests/{rid}").status_code == 404
    master = TestClient(client.app, follow_redirects=False)
    _make_master(master)
    page = master.get(f"/requests/{rid}")
    assert page.status_code == 404
    assert "переложить" not in page.text and ADDRESS not in page.text
    assert client.get("/requests/99999").status_code == 404
    assert client.get("/requests/abc").status_code in (404, 422)


def test_mine_lists_only_own_requests(client: TestClient) -> None:
    _signed_in(client)
    mine = _create(client)
    other = _other(client)
    theirs = _create(other)
    page = client.get("/requests/mine").text
    assert f'href="/requests/{mine}"' in page and f'href="/requests/{theirs}"' not in page
    assert client.get("/requests/mine").status_code == 200


def test_address_is_shown_only_on_the_clients_own_pages(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    _update(rid, responses_count=0)
    assert ADDRESS in client.get(f"/requests/{rid}").text
    # Every other page this task touches never carries it, whoever looks.
    pages = ["/requests/mine", "/requests/new", "/", "/dushanbe/santekhnik", "/search?q=плитка"]
    for who in (client, _other(client), TestClient(client.app, follow_redirects=False)):
        for path in pages:
            assert ADDRESS not in who.get(path).text, path
        assert ADDRESS not in who.get(f"/requests/{rid}/x").text
    other = _other(client, "carl@example.com")
    assert ADDRESS not in other.get(f"/requests/{rid}").text
    master = TestClient(client.app, follow_redirects=False)
    _make_master(master)
    assert ADDRESS not in master.get(f"/requests/{rid}").text
    # Failed form submissions of someone else do not echo it either.
    assert ADDRESS not in _post(other, f"/requests/{rid}/edit", _form()).text


# --- edit -------------------------------------------------------------------------------------


def test_edit_updates_the_request(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    ids = _ids()
    r = _post(
        client,
        f"/requests/{rid}/edit",
        _form(description="Новое описание работ, достаточно длинное", specialty=[str(ids["s2"])]),
    )
    assert r.status_code == 303 and r.headers["location"].startswith(f"/requests/{rid}")
    assert _row(rid).description.startswith("Новое описание")
    assert "Новое описание" in client.get(f"/requests/{rid}").text


def test_edit_errors_keep_the_form_and_change_nothing(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    r = _post(client, f"/requests/{rid}/edit", _form(description="коротко"))
    assert r.status_code == 422 and "коротко" in r.text and 'role="alert"' in r.text
    bad = _post(client, f"/requests/{rid}/edit", _form(timing="date", timing_date="2026-13-45"))
    assert bad.status_code == 422
    assert _row(rid).description.startswith("Нужно")


def test_edit_is_locked_after_a_response(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    _update(rid, responses_count=1)
    r = _post(client, f"/requests/{rid}/edit", _form(description="Другое описание, длинное"))
    assert r.status_code == 409 and 'role="alert"' in r.text
    assert _row(rid).description.startswith("Нужно")
    page = client.get(f"/requests/{rid}").text
    assert f'action="/requests/{rid}/edit"' not in page


def test_edit_by_someone_else_is_404_and_changes_nothing(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    other = _other(client)
    r = _post(other, f"/requests/{rid}/edit", _form(description="Чужое описание, длинное"))
    assert r.status_code == 404 and ADDRESS not in r.text
    assert _row(rid).description.startswith("Нужно")


# --- photos -----------------------------------------------------------------------------------


def test_upload_adds_photos_and_delete_removes_files(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    page = client.get(f"/requests/{rid}").text
    assert 'type="file"' in page
    r = _upload(client, rid, [("a.png", _png()), ("b.png", _png((70, 50)))])
    assert r.status_code == 303 and r.headers["location"].startswith(f"/requests/{rid}")
    photos = _photos(rid)
    assert len(photos) == 2
    keys = _stored_keys()
    assert photos[0].storage_key in keys and photos[0].thumb_key in keys
    assert "<img" in client.get(f"/requests/{rid}").text
    r = _post(client, f"/requests/{rid}/photos/{photos[0].id}/delete")
    assert r.status_code == 303
    assert len(_photos(rid)) == 1
    keys = _stored_keys()
    assert photos[0].storage_key not in keys and photos[0].thumb_key not in keys
    assert photos[1].storage_key in keys


def test_upload_reports_errors_by_position_and_keeps_good_files(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    r = _upload(client, rid, [("a.png", _png()), ("b.txt", b"not an image"), ("c.png", _png())])
    assert r.status_code == 200 and 'role="alert"' in r.text
    assert "2" in r.text.split('role="alert"')[1]
    assert len(_photos(rid)) == 2
    r = _upload(client, rid, [("x.txt", b"nope")])
    assert r.status_code == 422 and len(_photos(rid)) == 2


def test_upload_limit_is_four_photos(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    r = _upload(client, rid, [(f"{i}.png", _png((60 + i, 40))) for i in range(5)])
    assert r.status_code == 422  # more than 4 files in one go: refused as a whole
    assert _photos(rid) == []
    assert (
        _upload(client, rid, [(f"{i}.png", _png((60 + i, 40))) for i in range(3)]).status_code
        == 303
    )
    r = _upload(client, rid, [("1.png", _png((80, 40))), ("2.png", _png((81, 40)))])
    assert r.status_code == 200 and len(_photos(rid)) == 4  # one fitted, one did not
    assert 'role="alert"' in r.text
    r = _upload(client, rid, [("1.png", _png((90, 40)))])
    assert r.status_code == 422 and len(_photos(rid)) == 4


def test_upload_without_files_is_422(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    r = _post(client, f"/requests/{rid}/photos")
    assert r.status_code == 422


def test_uploads_off_hides_inputs_and_photo_post_is_404(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _signed_in(client)
    rid = _create(client)
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    assert _upload(client, rid, [("a.png", _png())]).status_code == 404
    assert _photos(rid) == []
    page = client.get(f"/requests/{rid}")
    assert page.status_code == 200 and 'type="file"' not in page.text
    assert client.get("/requests/new").status_code == 200
    assert _post(client, "/requests/new", _form()).status_code == 303
    assert (
        _post(
            client, f"/requests/{rid}/edit", _form(description="Описание подлиннее, да")
        ).status_code
        == 303
    )


def test_photos_of_someone_else_are_404(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    assert _upload(client, rid, [("a.png", _png())]).status_code == 303
    photo = _photos(rid)[0]
    other = _other(client)
    assert _upload(other, rid, [("a.png", _png())]).status_code == 404
    assert _post(other, f"/requests/{rid}/photos/{photo.id}/delete").status_code == 404
    assert len(_photos(rid)) == 1 and photo.storage_key in _stored_keys()
    # A photo id of another request does not resolve through this one.
    rid2 = _create(client)
    assert _post(client, f"/requests/{rid2}/photos/{photo.id}/delete").status_code == 404
    assert len(_photos(rid)) == 1


def test_upload_after_a_response_is_refused(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    _update(rid, responses_count=1)
    r = _upload(client, rid, [("a.png", _png())])
    assert r.status_code == 409 and _photos(rid) == [] and _stored_keys() == set()


def test_storage_failure_rolls_back_and_is_friendly(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.storage import StorageError

    _signed_in(client)
    rid = _create(client)

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise StorageError("down")

    monkeypatch.setattr("app.web.requests.requests.add_request_photos", boom)
    safe = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    r = _upload(safe, rid, [("a.png", _png())])
    assert r.status_code == 422 and 'role="alert"' in r.text
    assert _photos(rid) == []
    # The session is usable afterwards.
    assert safe.get(f"/requests/{rid}").status_code == 200


@pytest.mark.parametrize("which", ["os", "integrity"])
def test_other_storage_errors_are_friendly(client: TestClient, monkeypatch, which: str) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    _signed_in(client)
    rid = _create(client)

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk full") if which == "os" else IntegrityError("i", {}, Exception("dup"))

    monkeypatch.setattr("app.web.requests.requests.add_request_photos", boom)
    r = _upload(client, rid, [("a.png", _png())])
    assert r.status_code == 422 and "disk full" not in r.text
    assert _photos(rid) == []


def test_files_of_a_failed_commit_are_removed(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    _signed_in(client)
    rid = _create(client)
    real_commit = Session.commit

    def failing_commit(self):  # type: ignore[no-untyped-def]
        if _stored_keys():  # the files are already written: now the commit dies
            raise OperationalError("commit", {}, Exception("connection lost"))
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    safe = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    r = _upload(safe, rid, [("a.png", _png())])
    assert r.status_code in (422, 500) and "connection lost" not in r.text
    monkeypatch.undo()
    assert _photos(rid) == []
    assert _stored_keys() == set()


def test_photo_files_survive_a_failed_delete_commit(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    _signed_in(client)
    rid = _create(client)
    assert _upload(client, rid, [("a.png", _png())]).status_code == 303
    photo = _photos(rid)[0]

    def failing_commit(self):  # type: ignore[no-untyped-def]
        raise OperationalError("commit", {}, Exception("connection lost"))

    monkeypatch.setattr(Session, "commit", failing_commit)
    safe = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    r = _post(safe, f"/requests/{rid}/photos/{photo.id}/delete")
    assert r.status_code != 303
    monkeypatch.undo()
    assert len(_photos(rid)) == 1
    assert {photo.storage_key, photo.thumb_key} <= _stored_keys()


# --- close, cancel, reopen --------------------------------------------------------------------


def test_close_and_cancel(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    r = _post(client, f"/requests/{rid}/close")
    assert r.status_code == 303
    row = _row(rid)
    assert (row.status, row.close_reason) == ("closed", "client_closed") and row.closed_at
    rid2 = _create(client)
    assert _post(client, f"/requests/{rid2}/cancel").status_code == 303
    row = _row(rid2)
    assert (row.status, row.close_reason) == ("cancelled", "client_cancelled")
    page = client.get(f"/requests/{rid2}").text
    assert f'action="/requests/{rid2}/close"' not in page
    assert f'action="/requests/{rid2}/edit"' not in page


def test_double_close_is_a_friendly_message(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    assert _post(client, f"/requests/{rid}/close").status_code == 303
    safe = TestClient(client.app, follow_redirects=False, raise_server_exceptions=False)
    safe.cookies.update(client.cookies)
    for action in ("close", "cancel", "reopen"):
        r = _post(safe, f"/requests/{rid}/{action}")
        assert r.status_code == 409 and 'role="alert"' in r.text, action
    assert _row(rid).close_reason == "client_closed"


def test_reopen_needs_a_paused_request(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    assert f'action="/requests/{rid}/reopen"' not in client.get(f"/requests/{rid}").text
    assert _post(client, f"/requests/{rid}/reopen").status_code == 409
    _update(rid, feed_paused_at=utcnow() - timedelta(days=1), responses_count=15, feed_pause_at=15)
    page = client.get(f"/requests/{rid}").text
    assert f'action="/requests/{rid}/reopen"' in page
    assert _post(client, f"/requests/{rid}/reopen").status_code == 303
    row = _row(rid)
    assert row.feed_paused_at is None and row.feed_pause_at == 30
    assert _post(client, f"/requests/{rid}/reopen").status_code == 409


@pytest.mark.parametrize("action", ["close", "cancel", "reopen"])
def test_lifecycle_by_someone_else_is_404(client: TestClient, action: str) -> None:
    _signed_in(client)
    rid = _create(client)
    _update(rid, feed_paused_at=utcnow(), responses_count=15)
    other = _other(client)
    assert _post(other, f"/requests/{rid}/{action}").status_code == 404
    row = _row(rid)
    assert row.status == "open" and row.feed_paused_at is not None


# --- body limits ------------------------------------------------------------------------------


def test_request_photo_path_has_a_four_file_cap() -> None:
    assert REQUEST_UPLOAD_REQUEST_CAP == 4 * MAX_UPLOAD_BYTES + 64 * 1024
    assert request_cap("/requests/12/photos") == REQUEST_UPLOAD_REQUEST_CAP
    assert request_cap("/requests/12/photos/") == DEFAULT_CAP
    assert request_cap("/requests/abc/photos") == DEFAULT_CAP
    assert request_cap("/requests/12/photos/3/delete") == DEFAULT_CAP
    assert request_cap("/requests/new") == DEFAULT_CAP
    assert request_cap("/requests/12/edit") == DEFAULT_CAP
    assert request_cap("/master/avatar") == UPLOAD_REQUEST_CAP


def test_request_photo_cap_is_default_when_uploads_are_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("UPLOADS_ENABLED", "false")
    get_settings.cache_clear()
    assert request_cap("/requests/12/photos") == DEFAULT_CAP


def test_oversized_request_photo_body_is_413(client: TestClient) -> None:
    r = client.post(
        "/requests/1/photos",
        content=b"x" * (REQUEST_UPLOAD_REQUEST_CAP + 1),
        headers={"content-type": "x/y"},
    )
    assert r.status_code == 413


# --- entry points -----------------------------------------------------------------------------


def test_home_has_a_post_a_request_link(client: TestClient) -> None:
    page = client.get("/").text
    assert 'href="/requests/new"' in page


def test_seo_landing_links_to_the_form_with_its_city_and_specialty(client: TestClient) -> None:
    page = client.get("/dushanbe/santekhnik").text
    assert 'href="/requests/new?c=dushanbe&amp;s=santekhnik"' in page
    page = client.get("/tg/dushanbe/santekhnik").text
    assert 'href="/requests/new?c=dushanbe&amp;s=santekhnik"' in page


def test_account_links_to_requests(client: TestClient) -> None:
    _signed_in(client)
    page = client.get("/account").text
    assert 'href="/requests/mine"' in page and 'href="/requests/new"' in page


def test_tajik_pages_have_texts(client: TestClient) -> None:
    _signed_in(client)
    rid = _create(client)
    client.cookies.set("mc_lang", "tg")
    for path in ("/requests/new", "/requests/mine", f"/requests/{rid}"):
        text = client.get(path).text
        assert 'lang="tg"' in text
        assert "requests." not in re.sub(r"requests/", "", text), path
