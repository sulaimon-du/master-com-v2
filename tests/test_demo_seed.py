"""Demo accounts for staging (R-027): seed, idempotency, remove, guards, photos, no secrets."""

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import cli
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.security import verify_password
from app.core.time import utcnow
from app.models import (
    Category,
    City,
    Job,
    JobRequest,
    JobResponse,
    MasterArea,
    MasterProfile,
    MasterSearch,
    MasterSpecialty,
    PortfolioCollection,
    PortfolioPhoto,
    Specialty,
    User,
)
from app.seed import demo, demo_photos
from app.services import masters
from app.services import requests as requests_svc
from app.services.storage import LocalDiskStorage, get_storage

SEED_JSON = Path(demo_photos.MANIFEST).parent / "categories.json"


def _jpeg(width: int = 1000, height: int = 750) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buf, "JPEG")
    return buf.getvalue()


class FakeDownloader:
    def __init__(self, fail: bool = False, data: bytes | None = None) -> None:
        self.calls: list[str] = []
        self.fail, self.data = fail, data

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail:
            raise OSError("offline")
        return self.data if self.data is not None else _jpeg()


def _manifest() -> dict[str, list[dict]]:
    topics = {w.topic for m in demo.MASTERS for w in m.works}
    return {
        t: [
            {
                "specialty": t,
                "title": f"File:{t}.jpg",
                "url": f"https://upload.wikimedia.org/x/{t}.jpg",
                "license_code": "cc0",
                "license": "CC0",
            }
        ]
        for t in topics
    }


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "media")


@pytest.fixture
def accounts(tmp_path: Path) -> Path:
    return tmp_path / "secret" / "demo-accounts.txt"


def _seed(db: Session, storage, accounts: Path, **kw) -> demo.SeedStats:  # type: ignore[no-untyped-def]
    kw.setdefault("download", FakeDownloader())
    kw.setdefault("manifest", _manifest())
    return demo.seed(db, storage, accounts_file=accounts, **kw)


def _demo_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.email.like("demo-%@ustoiman.invalid"))))


def _key_file(storage: LocalDiskStorage, key: str) -> Path:
    return storage.path(key)


# --- manifest ---------------------------------------------------------------------------------


def test_every_manifest_entry_is_public_domain_or_cc0() -> None:
    raw = json.loads(demo_photos.MANIFEST.read_text(encoding="utf-8"))["photos"]
    assert 40 <= len(raw) <= 60
    slugs = {
        s["slug"]
        for c in json.loads(SEED_JSON.read_text(encoding="utf-8"))["categories"]
        for s in c["specialties"]
    }
    for entry in raw:
        assert demo_photos.license_ok(entry), entry["title"]
        assert (entry["license_code"], entry["license"]) in {
            ("pd", "Public domain"),
            ("cc0", "CC0"),
        }
        assert entry["specialty"] in slugs, entry["specialty"]
        assert entry["title"].startswith("File:")
        assert entry["source"].startswith("https://commons.wikimedia.org/wiki/File:")
        assert entry["author"].strip()
        assert "?" not in entry["url"]
    assert len({e["url"] for e in raw}) == len(raw)
    # Every topic a demo work uses has at least one real photo.
    assert {w.topic for m in demo.MASTERS for w in m.works} <= {e["specialty"] for e in raw}


def test_license_filter_drops_anything_but_pd_and_cc0(tmp_path: Path) -> None:
    good = {"specialty": "malyar", "title": "File:a.jpg", "license_code": "cc0", "license": "CC0"}
    pd = {"license_code": "pd", "license": "Public domain"}
    rows = [
        {**good, "url": "https://upload.wikimedia.org/a.jpg"},
        {**good, **pd, "url": "https://thumb.wikimedia.org/b.jpg"},
        {**good, "url": "https://upload.wikimedia.org/c.jpg", "license_code": "cc-by-sa-4.0"},
        {**good, "url": "https://upload.wikimedia.org/d.jpg", "license": "CC BY 4.0"},
        {**good, "url": "https://upload.wikimedia.org/g.jpg", "license": "Public domain"},
        {**good, "url": "https://upload.wikimedia.org/h.jpg", "license_code": "CC0"},
        {**good, "url": "https://example.com/e.jpg"},
        {**good, "url": "http://upload.wikimedia.org/f.jpg"},
    ]
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"photos": rows}), encoding="utf-8")
    kept = demo_photos.load_manifest(path)["malyar"]
    assert [e["url"].rsplit("/", 1)[1] for e in kept] == ["a.jpg", "b.jpg"]


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self.url = url

    def geturl(self) -> str:
        return self.url

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        return None


def _no_sleep(_s: float) -> None:
    return None


def _no_network(*a, **k):  # type: ignore[no-untyped-def]
    raise AssertionError("network used")


def test_downloader_refuses_other_hosts(tmp_path: Path) -> None:
    fetch = demo_photos.cached_downloader(tmp_path, opener=_no_network)
    for url in ("https://example.com/x.jpg", "http://upload.wikimedia.org/x.jpg"):
        with pytest.raises(ValueError):
            fetch(url)


def test_downloader_serves_from_cache_without_network(tmp_path: Path) -> None:
    url = "https://upload.wikimedia.org/x/y.jpg"
    fetch = demo_photos.cached_downloader(tmp_path, opener=_no_network)
    fetch.path(url).write_bytes(b"cached")
    assert fetch(url) == b"cached"
    fetch.forget(url)
    assert not fetch.path(url).exists()


def test_downloader_retries_after_429_then_caches(tmp_path: Path) -> None:
    url = "https://thumb.wikimedia.org/x/z.jpg"
    calls: list[str] = []

    def opener(request, timeout):  # type: ignore[no-untyped-def]
        calls.append(request.get_header("User-agent"))
        assert timeout == demo_photos.TIMEOUT
        if len(calls) == 1:
            raise demo_photos.urllib.error.HTTPError(url, 429, "slow down", {}, None)  # type: ignore[arg-type]
        return Response(b"photo", url)

    fetch = demo_photos.cached_downloader(tmp_path, sleep=_no_sleep, opener=opener)
    assert fetch(url) == b"photo"
    assert fetch(url) == b"photo"  # second time from the cache
    assert calls == ["UstoiMan-demo-seed/1.0"] * 2
    # Written atomically: only the final file, no temp files left behind.
    assert [p.name for p in tmp_path.iterdir()] == [fetch.path(url).name]


def test_downloader_gives_up_on_other_errors(tmp_path: Path) -> None:
    def opener(request, timeout):  # type: ignore[no-untyped-def]
        raise demo_photos.urllib.error.HTTPError("u", 404, "gone", {}, None)  # type: ignore[arg-type]

    with pytest.raises(demo_photos.urllib.error.HTTPError):
        demo_photos.cached_downloader(tmp_path, sleep=_no_sleep, opener=opener)(
            "https://upload.wikimedia.org/x/gone.jpg"
        )
    assert not list(tmp_path.iterdir())


def test_downloader_refuses_a_response_served_from_elsewhere(tmp_path: Path) -> None:
    def opener(request, timeout):  # type: ignore[no-untyped-def]
        return Response(b"evil", "https://evil.example/x.jpg")

    with pytest.raises(ValueError):
        demo_photos.cached_downloader(tmp_path, sleep=_no_sleep, opener=opener)(
            "https://upload.wikimedia.org/x/moved.jpg"
        )
    assert not list(tmp_path.iterdir())


def test_redirects_are_followed_only_to_commons() -> None:
    handler = demo_photos._CommonsOnlyRedirects()
    request = demo_photos.urllib.request.Request("https://upload.wikimedia.org/a.jpg")
    for target in ("https://evil.example/a.jpg", "http://upload.wikimedia.org/a.jpg"):
        with pytest.raises(demo_photos.urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "Found", {}, target)
    ok = handler.redirect_request(
        request, None, 302, "Found", {}, "https://thumb.wikimedia.org/a.jpg"
    )
    assert ok is not None and ok.full_url == "https://thumb.wikimedia.org/a.jpg"


# --- seed -------------------------------------------------------------------------------------


def test_seed_creates_published_masters_with_photos(db: Session, storage, accounts) -> None:
    download = FakeDownloader()
    stats = _seed(db, storage, accounts, download=download)
    assert stats.created == len(demo.MASTERS) == 10
    assert stats.clients_created == 3
    assert stats.photos == stats.real_photos > 0 and stats.generated_photos == 0
    assert download.calls  # the real-photo path was used

    profiles = db.scalars(
        select(MasterProfile)
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email.like("demo-%@ustoiman.invalid"))
    ).all()
    assert len(profiles) == 10
    for master in profiles:
        user = db.get(User, master.user_id)
        assert user is not None and user.email_verified_at is not None and user.blocked_at is None
        assert master.published_at is not None and master.hidden_at is None
        assert db.get(MasterSearch, master.id).is_visible
        assert master.bio and master.headline and master.experience_years
        # No contacts that could reach a real person.
        assert master.contact_telegram is None
        assert master.contact_whatsapp is None
        assert master.contact_instagram is None
        assert master.contact_phone is not None
        assert master.contact_phone.startswith(demo.PHONE_PREFIX)
        assert (
            1 <= db.scalar(select(func.count()).where(MasterSpecialty.master_id == master.id)) <= 3
        )
        # Avatar and photos are stored through the storage interface, re-encoded as WebP.
        assert master.avatar_key is not None
        assert _key_file(storage, master.avatar_key).read_bytes()[8:12] == b"WEBP"
        collections = db.scalars(
            select(PortfolioCollection).where(PortfolioCollection.master_id == master.id)
        ).all()
        assert 3 <= len(collections) <= 6
        for collection in collections:
            photos = db.scalars(
                select(PortfolioPhoto).where(PortfolioPhoto.collection_id == collection.id)
            ).all()
            assert 1 <= len(photos) <= 3
            for photo in photos:
                assert _key_file(storage, photo.storage_key).read_bytes()[8:12] == b"WEBP"
                assert _key_file(storage, photo.thumb_key).is_file()
        assert db.get(MasterSearch, master.id).has_photos

    cities = db.scalars(
        select(City.slug)
        .join(MasterArea, MasterArea.city_id == City.id)
        .where(MasterArea.master_id.in_([m.id for m in profiles]))
        .distinct()
    ).all()
    assert "dushanbe" in cities and len(cities) >= 3
    categories = db.scalars(
        select(Category.id)
        .join(Specialty, Specialty.category_id == Category.id)
        .join(MasterSpecialty, MasterSpecialty.specialty_id == Specialty.id)
        .where(MasterSpecialty.master_id.in_([m.id for m in profiles]))
        .distinct()
    ).all()
    assert len(categories) >= 4

    clients = [u for u in _demo_users(db) if u.email.startswith("demo-client-")]
    assert len(clients) == 3 and all(u.phone is None for u in clients)
    open_requests = db.scalars(
        select(JobRequest).where(JobRequest.client_id.in_([u.id for u in clients]))
    ).all()
    assert len(open_requests) == 3
    assert all(r.status == "open" and r.address is None for r in open_requests)
    # Demo requests do not notify anyone.
    assert not db.scalar(
        select(func.count()).where(
            Job.payload["request_id"].as_integer().in_([r.id for r in open_requests])
        )
    )


def test_downloaded_bytes_go_through_the_upload_pipeline(db: Session, storage, accounts) -> None:
    # A 3000x2000 JPEG with EXIF: the pipeline must shrink it to 1600 px and drop the metadata.
    img = Image.new("RGB", (3000, 2000), (10, 200, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    _seed(
        db, storage, accounts, count=1, clients=False, download=FakeDownloader(data=buf.getvalue())
    )
    photo = db.scalars(
        select(PortfolioPhoto)
        .join(PortfolioCollection, PortfolioCollection.id == PortfolioPhoto.collection_id)
        .join(MasterProfile, MasterProfile.id == PortfolioCollection.master_id)
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email == demo.master_email(1))
    ).first()
    assert photo is not None and (photo.width, photo.height) == (1600, 1067)
    stored = _key_file(storage, photo.storage_key).read_bytes()
    assert stored[8:12] == b"WEBP" and b"SecretCam" not in stored


def test_failed_downloads_fall_back_to_generated_images(db: Session, storage, accounts) -> None:
    download = FakeDownloader(fail=True)
    stats = _seed(db, storage, accounts, count=1, clients=False, download=download)
    assert stats.photos > 0 and stats.real_photos == 0
    assert stats.generated_photos == stats.photos
    # A failed URL is not retried for every photo.
    assert len(download.calls) == len(set(download.calls))


class ForgettingDownloader(FakeDownloader):
    def __init__(self, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.forgotten: list[str] = []

    def forget(self, url: str) -> None:
        self.forgotten.append(url)


def test_unreadable_real_photo_falls_back_and_is_forgotten(db: Session, storage, accounts) -> None:
    download = ForgettingDownloader(data=b"not an image")
    stats = _seed(db, storage, accounts, count=1, clients=False, download=download)
    assert stats.photos > 0 and stats.generated_photos == stats.photos
    # The refused URL's cache file is dropped and the URL is not tried again in this run.
    assert download.forgotten and len(download.forgotten) == len(set(download.forgotten))
    assert set(download.calls) == set(download.forgotten)
    assert len(download.calls) == len(set(download.calls))


def test_refused_cached_file_is_deleted(tmp_path: Path) -> None:
    url = "https://upload.wikimedia.org/x/bad.jpg"
    fetch = demo_photos.cached_downloader(tmp_path, opener=_no_network)
    fetch.path(url).write_bytes(b"not an image")
    photos = demo._Photos({"plitochnik": [{"url": url}]}, fetch)
    data, got = photos.next("plitochnik")
    assert got == url and data == b"not an image"
    photos.reject(url)
    assert not fetch.path(url).exists()
    assert photos.next("plitochnik")[1] is None  # generated from now on


def test_missing_places_are_a_friendly_error(db: Session) -> None:
    with pytest.raises(demo.DemoError):
        demo._place(db, "no-such-city", None)
    with pytest.raises(demo.DemoError):
        demo._place(db, "dushanbe", "no-such-district")
    with pytest.raises(demo.DemoError):
        demo._specialty_ids(db, ("no-such-specialty",))


def test_no_manifest_photo_uses_generated_images(db: Session, storage, accounts) -> None:
    download = FakeDownloader()
    stats = _seed(db, storage, accounts, count=1, clients=False, manifest={}, download=download)
    assert download.calls == [] and stats.generated_photos == stats.photos > 0


def test_generated_images_are_1200x900() -> None:
    for topic in ("plitochnik", "kamenshchik", "elektrik", "unknown-topic"):
        assert Image.open(io.BytesIO(demo_photos.generated_photo(topic, 1))).size == (1200, 900)
    assert Image.open(io.BytesIO(demo_photos.avatar("FN", 1))).size == (400, 400)


def test_reseed_is_idempotent_and_keeps_the_password(db: Session, storage, accounts) -> None:
    first = _seed(db, storage, accounts, count=2)
    users = len(_demo_users(db))
    password = demo._read_password(accounts)
    second = _seed(db, storage, accounts, count=2)
    assert (first.created, second.created, second.kept) == (2, 0, 2)
    assert (second.clients_created, second.clients_kept) == (0, 3)
    assert second.photos == 0
    assert len(_demo_users(db)) == users == 5
    assert demo._read_password(accounts) == password


def test_password_file_is_written_and_matches(db: Session, storage, accounts) -> None:
    _seed(db, storage, accounts, count=1)
    text = accounts.read_text(encoding="utf-8")
    password = demo._read_password(accounts)
    assert password and len(password) >= 16
    assert demo.master_email(1) in text and demo.client_email(1) in text
    assert accounts.stat().st_mode & 0o077 == 0
    user = db.scalar(select(User).where(User.email == demo.master_email(1)))
    assert user is not None and verify_password(user.password_hash, password)


def test_lost_password_file_resets_demo_passwords(db: Session, storage, accounts) -> None:
    _seed(db, storage, accounts, count=1, clients=False)
    accounts.unlink()
    _seed(db, storage, accounts, count=1, clients=False)
    user = db.scalar(select(User).where(User.email == demo.master_email(1)))
    assert user is not None and verify_password(user.password_hash, demo._read_password(accounts))


def test_count_is_checked(db: Session, storage, accounts) -> None:
    for bad in (0, len(demo.MASTERS) + 1):
        with pytest.raises(demo.DemoError):
            _seed(db, storage, accounts, count=bad)


# --- remove -----------------------------------------------------------------------------------


def _real_master(db: Session) -> MasterProfile:
    user = User(
        email="real-master@example.com",
        name="Настоящий",
        phone="+992900000001",
        phone_verified_at=utcnow(),
    )
    db.add(user)
    db.flush()
    master = masters.create_profile(
        db,
        user,
        masters.BasicsInput(
            display_name="Настоящий Мастер",
            slug="real-master-x",
            headline=None,
            experience_years=1,
            is_available=True,
        ),
    )
    plitochnik = db.scalar(select(Specialty.id).where(Specialty.slug == "plitochnik"))
    masters.set_specialties(db, master, [plitochnik], [])
    dushanbe = db.scalar(select(City.id).where(City.slug == "dushanbe"))
    masters.set_areas(db, master, [dushanbe], [])
    return master


def test_remove_deletes_demo_rows_and_files_and_nothing_else(
    db: Session, storage, accounts
) -> None:
    real = _real_master(db)
    _seed(db, storage, accounts, count=2)
    demo_master = db.scalar(
        select(MasterProfile)
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email == demo.master_email(1))
    )
    assert demo_master is not None
    # A real client's request with a demo master's response: the request stays, counter fixed.
    client = User(email="real-client@example.com", name="Клиент")
    db.add(client)
    db.flush()
    dushanbe = db.scalar(select(City.id).where(City.slug == "dushanbe"))
    request = JobRequest(
        client_id=client.id,
        city_id=dushanbe,
        district_id=demo_master
        and db.scalar(select(MasterArea.district_id).where(MasterArea.master_id == demo_master.id)),
        description="Реальная заявка клиента на укладку плитки",
        timing="flexible",
        responses_count=1,
    )
    db.add(request)
    db.flush()
    db.add(JobResponse(request_id=request.id, master_id=demo_master.id))
    db.flush()

    files = [p for p in (storage.root).rglob("*.webp")]
    assert files

    stats = demo.remove(db)
    assert (stats.users, stats.masters, stats.requests) == (5, 2, 3)
    db.flush()
    assert demo.delete_files(storage, stats.keys) == 0

    assert _demo_users(db) == []
    assert not [p for p in storage.root.rglob("*.webp")]
    assert db.get(MasterProfile, real.id) is not None
    assert db.get(MasterSearch, real.id) is not None
    assert db.scalar(select(User).where(User.email == "real-client@example.com")) is not None
    db.refresh(request)
    assert request.responses_count == 0
    assert not db.scalar(select(func.count()).where(JobResponse.request_id == request.id))
    # Nothing demo is left in the search index or the portfolio tables.
    assert not db.scalar(
        select(func.count())
        .select_from(MasterSearch)
        .where(~MasterSearch.master_id.in_(select(MasterProfile.id)))
    )


def test_remove_refuses_when_a_real_request_chose_a_demo_master(
    db: Session, storage, accounts
) -> None:
    _seed(db, storage, accounts, count=1, clients=False)
    demo_master = db.scalar(
        select(MasterProfile)
        .where(MasterProfile.slug.is_not(None))
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email == demo.master_email(1))
    )
    assert demo_master is not None
    client = User(email="real-client2@example.com", name="Клиент")
    db.add(client)
    db.flush()
    city = db.scalar(select(City.id).where(City.slug == "dushanbe"))
    district = db.scalar(
        select(MasterArea.district_id).where(MasterArea.master_id == demo_master.id)
    )
    db.add(
        JobRequest(
            client_id=client.id,
            city_id=city,
            district_id=district,
            description="Заявка, где выбран демо-мастер",
            timing="flexible",
            status="master_chosen",
            chosen_master_id=demo_master.id,
            master_chosen_at=utcnow(),
            client_phone="+992900000002",
        )
    )
    db.flush()
    blocked = db.scalar(select(JobRequest.id).where(JobRequest.client_id == client.id))
    with pytest.raises(demo.DemoError, match=str(blocked)):
        demo.remove(db)
    assert len(_demo_users(db)) == 1


@pytest.mark.parametrize("status", ["closed", "cancelled"])
def test_remove_unlinks_closed_real_requests_that_chose_a_demo_master(
    db: Session, storage, accounts, status: str
) -> None:
    _seed(db, storage, accounts, count=1, clients=False)
    demo_master = db.scalar(
        select(MasterProfile)
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email == demo.master_email(1))
    )
    assert demo_master is not None
    client = User(email="real-client3@example.com", name="Клиент")
    db.add(client)
    db.flush()
    city = db.scalar(select(City.id).where(City.slug == "dushanbe"))
    district = db.scalar(
        select(MasterArea.district_id).where(MasterArea.master_id == demo_master.id)
    )
    request = JobRequest(
        client_id=client.id,
        city_id=city,
        district_id=district,
        description="Заявка, где выбран демо-мастер, потом закрыта",
        timing="flexible",
        status="master_chosen",
        chosen_master_id=demo_master.id,
        master_chosen_at=utcnow(),
        client_phone="+992900000003",
    )
    db.add(request)
    db.flush()
    # The real way out: the client closes the request; `close_request` keeps the chosen master.
    requests_svc.close_request(db, client, request.id, status)  # type: ignore[arg-type]
    db.flush()
    assert request.status == status and request.chosen_master_id == demo_master.id

    stats = demo.remove(db)
    db.flush()
    assert stats.unlinked == 1 and stats.masters == 1
    assert _demo_users(db) == []
    db.refresh(request)
    assert request.status == status
    assert request.chosen_master_id is None and request.master_chosen_at is None
    assert request.closed_at is not None and request.client_id == client.id


def test_remove_with_no_demo_data_is_a_no_op(db: Session) -> None:
    assert demo.remove(db).users == 0


# --- the command ------------------------------------------------------------------------------


def test_other_commands_do_not_import_demo_code() -> None:
    import subprocess
    import sys

    code = "import sys, app.cli; print('app.seed.demo' in sys.modules)"
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
    assert len(demo.MASTERS) == cli.DEMO_COUNT


def test_production_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SITE_URL", "https://ustoiman.example")
    get_settings.cache_clear()
    assert cli.main(["demo", "seed"]) == 2
    assert cli.main(["demo", "remove"]) == 2
    assert "production" in capsys.readouterr().err


def test_staging_without_r2_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    get_settings.cache_clear()
    assert cli.main(["demo", "seed"]) == 2
    assert "R2" in capsys.readouterr().err


def test_cli_seed_and_remove_never_print_the_password(
    clean_auth, tmp_path: Path, monkeypatch, capsys
) -> None:
    accounts = tmp_path / "accounts.txt"
    monkeypatch.setattr(demo, "ACCOUNTS_FILE", accounts)
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo_photos, "cached_downloader", lambda _dir: FakeDownloader())
    try:
        assert cli.main(["demo", "seed", "--count", "1"]) == 0
        out = capsys.readouterr()
        password = demo._read_password(accounts)
        assert password and password not in out.out + out.err
        assert "1 created" in out.out
        target = next(line for line in out.out.splitlines() if line.startswith("target:"))
        url = make_url(str(get_settings().database_url))
        assert f"database={url.host}/{url.database}" in target
        assert url.username not in target
        if url.password:
            assert url.password not in out.out
        with get_sessionmaker()() as db:
            assert len(_demo_users(db)) == 4
    finally:
        assert cli.main(["demo", "remove"]) == 0
    out = capsys.readouterr()
    assert "removed 4 demo users" in out.out and password not in out.out + out.err
    with get_sessionmaker()() as db:
        assert _demo_users(db) == []
    storage = get_storage()
    assert isinstance(storage, LocalDiskStorage)
    assert not list(storage.root.rglob("*.webp"))


# --- jobs (the real queue since PR C; ledger demo M6) -----------------------------------------


def _jobs_for(db: Session, request_ids: list[int]) -> list[Job]:
    return list(
        db.scalars(select(Job).where(Job.payload["request_id"].as_integer().in_(request_ids)))
    )


def test_seeding_queues_no_live_notification_jobs(
    db: Session, storage, accounts, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    queued: list[tuple[str, dict]] = []
    real_enqueue = requests_svc.jobs.enqueue

    def spy(db_, kind, payload, **kw):  # type: ignore[no-untyped-def]
        queued.append((kind, payload))
        return real_enqueue(db_, kind, payload, **kw)

    monkeypatch.setattr(requests_svc.jobs, "enqueue", spy)
    _seed(db, storage, accounts, count=2)
    db.flush()
    request_ids = list(
        db.scalars(
            select(JobRequest.id)
            .join(User, User.id == JobRequest.client_id)
            .where(User.email.like("demo-client-%"))
        )
    )
    assert len(request_ids) == 3
    # create_request really queued the fan-out (the same kind and payload as the site) ...
    assert sorted(p["request_id"] for k, p in queued if k == "new_request") == sorted(request_ids)
    # ... and the seed dropped it: no job about a demo request is left to run.
    assert _jobs_for(db, request_ids) == []
    assert not db.scalar(
        select(func.count()).select_from(Job).where(Job.kind.in_(("new_request", "notify")))
    )


def test_remove_deletes_every_job_about_demo_rows_and_keeps_real_ones(
    db: Session, storage, accounts
) -> None:
    from app.services import jobs

    _seed(db, storage, accounts, count=2)
    demo_request = db.scalar(
        select(JobRequest)
        .join(User, User.id == JobRequest.client_id)
        .where(User.email.like("demo-client-%"))
    )
    demo_master = db.scalar(
        select(MasterProfile)
        .join(User, User.id == MasterProfile.user_id)
        .where(User.email == demo.master_email(1))
    )
    assert demo_request is not None and demo_master is not None
    client = User(email="real-client-jobs@example.com", name="Клиент")
    db.add(client)
    db.flush()
    real_request = JobRequest(
        client_id=client.id,
        city_id=demo_request.city_id,
        description="Реальная заявка клиента на укладку плитки",
        timing="flexible",
        responses_count=1,
    )
    db.add(real_request)
    db.flush()
    response = JobResponse(request_id=real_request.id, master_id=demo_master.id)
    db.add(response)
    db.flush()
    # What Tasks 7-8 queue: event jobs (ids only) and per-recipient notify jobs.
    jobs.enqueue(db, "new_request", {"request_id": demo_request.id}, dedupe_key="t:a")
    jobs.enqueue(db, "new_response", {"response_id": response.id}, dedupe_key="t:b")
    jobs.enqueue(
        db,
        "notify",
        {
            "event": "new_request",
            "request_id": real_request.id,
            "user_id": demo_master.user_id,
            "channel": "telegram",
        },
        dedupe_key="t:c",
    )
    jobs.enqueue(db, "new_request", {"request_id": real_request.id}, dedupe_key="t:keep")
    db.flush()

    demo.remove(db)
    db.flush()

    left = db.scalars(select(Job.dedupe_key).where(Job.dedupe_key.like("t:%"))).all()
    assert left == ["t:keep"]
