import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import (
    City,
    MasterProfile,
    MasterSpecialty,
    PortfolioPhoto,
    Specialty,
    User,
)
from app.services import masters, portfolio
from app.services.storage import get_storage
from tests.support import PASSWORD, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

PHONE = "+992901112233"
TELEGRAM = "ali_master"
WHATSAPP = "+992935556677"
INSTAGRAM = "ali.builds"


def _make_master(
    email: str = "owner@example.com",
    slug: str = "ali-rahimov",
    *,
    publish: bool = True,
    phone: str = PHONE,
) -> int:
    uid = make_user(email)
    _known.add(email)
    with get_sessionmaker()() as db:
        user = db.get(User, uid)
        if publish:
            user.phone, user.phone_verified_at = phone, utcnow()
        master = masters.create_profile(
            db,
            user,
            masters.BasicsInput(
                display_name="Али Рахимов",
                slug=slug,
                headline="Плиточник",
                experience_years=5,
                is_available=True,
            ),
        )
        if publish:
            specs = db.scalars(select(Specialty).where(Specialty.is_active).limit(2)).all()
            masters.set_specialties(
                db, master, [specs[0].id, specs[1].id], primary_ids=[specs[1].id]
            )
            city = db.scalars(select(City).where(City.slug == "dushanbe")).one()
            masters.set_areas(db, master, [city.id], [])
        masters.set_contacts(db, master, telegram=TELEGRAM, whatsapp=WHATSAPP, instagram=INSTAGRAM)
        db.commit()
    return uid


def _client(client: TestClient, email: str) -> TestClient:
    other = TestClient(client.app, follow_redirects=False)
    if email not in _known:
        make_user(email)
        _known.add(email)
    assert sign_in(other, email).status_code == 303
    return other


_known: set[str] = set()


@pytest.fixture(autouse=True)
def _reset_known() -> None:
    _known.clear()


def test_published_profile_is_public(client: TestClient) -> None:
    _make_master()
    r = client.get("/m/ali-rahimov")
    assert r.status_code == 200
    assert "Али Рахимов" in r.text and "Плиточник" in r.text
    assert "<title>Али Рахимов · " in r.text
    assert 'name="robots"' not in r.text


def test_draft_is_404_for_others_and_visible_to_owner_with_banner(client: TestClient) -> None:
    _make_master(publish=False)
    assert client.get("/m/ali-rahimov").status_code == 404
    stranger = _client(client, "x@example.com")
    assert stranger.get("/m/ali-rahimov").status_code == 404
    owner = _client(client, "owner@example.com")
    r = owner.get("/m/ali-rahimov")
    assert r.status_code == 200
    assert "Черновик" in r.text and 'content="noindex"' in r.text
    assert "/master/onboarding/phone" in r.text


def test_hidden_profile_404(client: TestClient) -> None:
    _make_master()
    with get_sessionmaker()() as db:
        m = db.scalars(select(MasterProfile)).one()
        m.hidden_at = utcnow()
        db.commit()
    assert client.get("/m/ali-rahimov").status_code == 404
    assert client.get("/m/nobody").status_code == 404


def test_guest_sees_sign_in_prompt_and_no_contact_values(client: TestClient) -> None:
    _make_master()
    r = client.get("/m/ali-rahimov")
    for value in (PHONE, PHONE[1:], TELEGRAM, WHATSAPP, WHATSAPP[1:], INSTAGRAM):
        assert value not in r.text
    assert "/signin?next=/m/ali-rahimov" in r.text


def test_signed_in_user_sees_contacts(client: TestClient) -> None:
    _make_master()
    viewer = _client(client, "viewer@example.com")
    r = viewer.get("/m/ali-rahimov")
    assert f'href="tel:{PHONE}"' in r.text
    assert f'href="https://t.me/{TELEGRAM}"' in r.text
    assert f'href="https://wa.me/{WHATSAPP[1:]}"' in r.text
    assert f'href="https://instagram.com/{INSTAGRAM}"' in r.text
    assert "/signin?next=" not in r.text


def test_sign_in_link_returns_to_profile(client: TestClient) -> None:
    _make_master()
    r = client.get("/m/ali-rahimov")
    link = "/signin?next=/m/ali-rahimov"
    assert link in r.text
    assert client.get(link).status_code == 200
    make_user("back@example.com")
    r = post(
        client,
        "/signin",
        {"email": "back@example.com", "password": PASSWORD, "next": "/m/ali-rahimov"},
    )
    assert r.status_code == 303 and r.headers["location"] == "/m/ali-rahimov"


def test_specialties_in_viewer_language_primary_first(client: TestClient) -> None:
    _make_master()
    with get_sessionmaker()() as db:
        primary = db.scalars(
            select(Specialty)
            .join(MasterSpecialty, MasterSpecialty.specialty_id == Specialty.id)
            .where(MasterSpecialty.is_primary)
        ).one()
        other = db.scalars(
            select(Specialty)
            .join(MasterSpecialty, MasterSpecialty.specialty_id == Specialty.id)
            .where(MasterSpecialty.is_primary.is_(False))
        ).one()
        ru = (primary.name_ru, other.name_ru)
        tg = (primary.name_tg or primary.name_ru, other.name_tg or other.name_ru)
    text = client.get("/m/ali-rahimov").text
    assert text.index(ru[0]) < text.index(ru[1])
    client.get("/lang/tg")
    text = client.get("/m/ali-rahimov").text
    assert text.index(tg[0]) < text.index(tg[1])


def test_notices_only_for_owner(client: TestClient) -> None:
    _make_master(publish=False)
    owner = _client(client, "owner@example.com")
    r = owner.get("/m/ali-rahimov?notice=incomplete")
    assert "/master/onboarding/specialties" in r.text and "/master/onboarding/area" in r.text
    assert owner.get("/m/ali-rahimov?notice=bogus").status_code == 200
    _make_master("pub@example.com", "pub-master")
    guest = client.get("/m/pub-master?notice=published")
    owner2 = _client(client, "pub@example.com")
    assert guest.text.count("notice-published") == 0
    assert "notice-published" in owner2.get("/m/pub-master?notice=published").text


def test_profile_responses_are_not_cached(client: TestClient) -> None:
    _make_master()
    _make_master("d@example.com", "draft-one", publish=False)
    viewer = _client(client, "viewer@example.com")
    for c, slug in (
        (client, "ali-rahimov"),
        (viewer, "ali-rahimov"),
        (client, "draft-one"),
        (client, "nobody"),
    ):
        assert c.get(f"/m/{slug}").headers["cache-control"] == "no-store"


# --- public collections and the gallery page --------------------------------------------------


def _png(size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(buf, "PNG")
    return buf.getvalue()


def _add_collection(
    title: str = "Кухня", photos: int = 2, *, hidden: bool = False, slug: str = "ali-rahimov"
) -> tuple[int, list[int]]:
    """A collection with `photos` live photos for the master `slug`; returns (id, photo ids)."""
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile).where(MasterProfile.slug == slug)).one()
        collection = portfolio.create_collection(db, master, title, "Плитка и фартук")
        ids = [
            portfolio.add_photo(db, get_storage(), master, collection.id, _png((64 + i, 48))).id
            for i in range(photos)
        ]
        if hidden:
            portfolio.set_collection_hidden(db, master, collection.id, True)
        db.commit()
        return collection.id, ids


def test_profile_shows_square_grid_of_public_collections(client: TestClient) -> None:
    _make_master()
    shown, photo_ids = _add_collection("Кухня")
    _add_collection("Спрятанная", hidden=True)
    _add_collection("Пустая", photos=0)
    with get_sessionmaker()() as db:
        cover = db.get(PortfolioPhoto, photo_ids[0]).thumb_key
    text = client.get("/m/ali-rahimov").text
    assert "Кухня" in text
    assert "Спрятанная" not in text and "Пустая" not in text
    assert f"/m/ali-rahimov/c/{shown}" in text
    assert f"/media/{cover}" in text
    assert "aspect-square" in text and "object-cover" in text
    assert "/master/portfolio" not in text  # the manage link is for the owner only


def test_owner_sees_manage_portfolio_link(client: TestClient) -> None:
    _make_master()
    owner = _client(client, "owner@example.com")
    assert 'href="/master/portfolio"' in owner.get("/m/ali-rahimov").text


def test_collection_page_lists_photos_in_order_without_deleted(client: TestClient) -> None:
    _make_master()
    cid, ids = _add_collection(photos=3)
    with get_sessionmaker()() as db:
        photos = [db.get(PortfolioPhoto, i) for i in ids]
        photos[1].deleted_at = utcnow()
        keys = [photos[0].storage_key, photos[1].storage_key, photos[2].storage_key]
        db.commit()
    r = client.get(f"/m/ali-rahimov/c/{cid}")
    assert r.status_code == 200
    assert "Кухня" in r.text and "Плитка и фартук" in r.text
    assert f"/media/{keys[1]}" not in r.text
    assert r.text.index(f"/media/{keys[0]}") < r.text.index(f"/media/{keys[2]}")
    assert 'id="photo-1"' in r.text and 'id="photo-2"' in r.text and 'id="photo-3"' not in r.text
    assert 'href="#photo-2"' in r.text
    assert "1 / 2" in r.text
    assert 'loading="lazy"' in r.text and 'width="64"' in r.text and 'height="48"' in r.text
    assert 'href="/m/ali-rahimov"' in r.text and "Али Рахимов" in r.text


def test_hidden_collection_404(client: TestClient) -> None:
    _make_master()
    cid, _ = _add_collection(hidden=True)
    assert client.get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    assert client.get("/m/ali-rahimov/c/999999").status_code == 404
    assert _client(client, "x@example.com").get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    owner = _client(client, "owner@example.com")
    assert owner.get(f"/m/ali-rahimov/c/{cid}").status_code == 200


def test_collection_of_another_master_is_404(client: TestClient) -> None:
    _make_master()
    _make_master("other@example.com", slug="other-master", phone="+992902223344")
    cid, _ = _add_collection("Кухня", slug="other-master")
    assert client.get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    assert client.get(f"/m/other-master/c/{cid}").status_code == 200


def test_collection_of_draft_master_404_for_others(client: TestClient) -> None:
    _make_master(publish=False)
    cid, _ = _add_collection()
    assert client.get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    assert _client(client, "x@example.com").get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    r = _client(client, "owner@example.com").get(f"/m/ali-rahimov/c/{cid}")
    assert r.status_code == 200 and 'content="noindex"' in r.text


def test_empty_visible_collection_404_for_others_200_for_owner(client: TestClient) -> None:
    _make_master()
    cid, ids = _add_collection(photos=1)
    with get_sessionmaker()() as db:
        db.get(PortfolioPhoto, ids[0]).deleted_at = utcnow()
        db.commit()
    assert client.get(f"/m/ali-rahimov/c/{cid}").status_code == 404
    assert _client(client, "owner@example.com").get(f"/m/ali-rahimov/c/{cid}").status_code == 200


def test_moderated_photo_only_for_owner(client: TestClient) -> None:
    _make_master()
    cid, ids = _add_collection(photos=2)
    with get_sessionmaker()() as db:
        photo = db.get(PortfolioPhoto, ids[1])
        photo.hidden_at, key = utcnow(), photo.storage_key
        db.commit()
    assert f"/media/{key}" not in client.get(f"/m/ali-rahimov/c/{cid}").text
    owner = _client(client, "owner@example.com")
    assert f"/media/{key}" in owner.get(f"/m/ali-rahimov/c/{cid}").text


def test_collection_page_has_no_inline_script_or_style(client: TestClient) -> None:
    _make_master()
    cid, _ = _add_collection()
    text = client.get(f"/m/ali-rahimov/c/{cid}").text
    assert not re.search(r"<script(?![^>]*\bsrc=)", text)
    assert "style=" not in text
    assert "x-html" not in text


def test_collection_page_is_no_store_and_loads_gallery_js_only_there(client: TestClient) -> None:
    _make_master()
    cid, _ = _add_collection()
    r = client.get(f"/m/ali-rahimov/c/{cid}")
    assert r.headers["cache-control"] == "no-store"
    assert "/static/js/gallery.js" in r.text and 'x-data="gallery"' in r.text
    assert "/static/js/gallery.js" not in client.get("/m/ali-rahimov").text
    js = client.get("/static/js/gallery.js")
    assert js.status_code == 200 and "Alpine.data" in js.text and "gallery" in js.text
