"""Meta tags on profiles and collections, robots.txt and no indexing outside production
(spec D5, D7; Review Focus 5)."""

import re
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.main import create_app
from app.models import MasterProfile
from app.services import masters, seo
from tests.test_profile_web import _add_collection, _make_master

pytestmark = pytest.mark.usefixtures("clean_auth")

SITE = "https://ustoi.example"
AVATAR_KEY = "a/" + "0" * 32 + ".webp"


@pytest.fixture(autouse=True)
def _site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITE_URL", SITE + "/")
    get_settings.cache_clear()


def _attr(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    assert match is not None, pattern
    return unescape(match.group(1))


def _og(html: str, prop: str) -> str:
    return _attr(html, rf'<meta property="og:{prop}" content="([^"]*)">')


def _title(html: str) -> str:
    return _attr(html, r"<title>([^<]*)</title>")


def _description(html: str) -> str:
    return _attr(html, r'<meta name="description" content="([^"]*)">')


def _canonical(html: str) -> str:
    return _attr(html, r'<link rel="canonical" href="([^"]*)">')


def _app(monkeypatch: pytest.MonkeyPatch, env: str) -> TestClient:
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    return TestClient(create_app(Settings()), follow_redirects=False)  # type: ignore[call-arg]


def _set(**fields: object) -> None:
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile)).one()
        for name, value in fields.items():
            setattr(master, name, value)
        db.commit()


# --- profile ----------------------------------------------------------------------------------


def test_profile_meta(client: TestClient) -> None:
    _make_master()
    html = client.get("/m/ali-rahimov").text
    title = _title(html)
    assert title.startswith("Али Рахимов — ") and ", Душанбе · " in title
    assert 0 < len(_description(html)) <= 160 and "Мастер на все руки" in _description(html)
    assert _canonical(html) == f"{SITE}/m/ali-rahimov"
    assert _attr(html, r'<meta name="robots" content="([^"]*)">') == "index, follow"
    assert _og(html, "title") == title
    assert _og(html, "description") == _description(html)
    assert _og(html, "url") == f"{SITE}/m/ali-rahimov"
    assert _og(html, "type") == "profile"
    assert _og(html, "locale") == "ru_RU"
    assert "og:image" not in html  # no avatar, no photo
    assert "hreflang" not in html.split("</head>")[0].replace('hreflang="ru"', "")


def test_profile_meta_in_tajik(client: TestClient) -> None:
    _make_master()
    client.cookies.set("mc_lang", "tg")
    html = client.get("/m/ali-rahimov").text
    assert _og(html, "locale") == "tg_TJ"
    assert "{" not in _title(html) and "UstoiMan" in _title(html)


def test_profile_og_image_is_avatar_made_absolute(client: TestClient) -> None:
    _make_master()
    _set(avatar_key=AVATAR_KEY)
    html = client.get("/m/ali-rahimov").text
    assert _og(html, "image") == f"{SITE}/media/{AVATAR_KEY}"


def test_profile_og_image_falls_back_to_first_public_photo_thumb(client: TestClient) -> None:
    _make_master()
    _add_collection("Кухня", photos=2)
    html = client.get("/m/ali-rahimov").text
    image = _og(html, "image")
    assert image.startswith(f"{SITE}/media/") and image.endswith("-t.webp")


def test_long_bio_description_is_clipped_plain_text(client: TestClient) -> None:
    _make_master()
    _set(bio='<b>Очень</b> "длинное" описание ' * 25)
    description = _description(client.get("/m/ali-rahimov").text)
    assert len(description) <= 160
    assert "\n" not in description


def test_profile_without_specialty_or_city_still_has_title(client: TestClient) -> None:
    _make_master(publish=False)
    with get_sessionmaker()() as db:
        master = db.scalars(select(MasterProfile)).one()
        view = masters.profile_view(db, master, lang="ru", viewer=None)
    meta = seo.profile_meta(view, lang="ru")
    assert meta.title.startswith("Али Рахимов") and "{" not in meta.title
    assert meta.robots == "noindex"  # a draft is never indexed


def test_draft_profile_keeps_noindex(client: TestClient) -> None:
    from tests.test_profile_web import _client

    _make_master(publish=False)
    owner = _client(client, "owner@example.com")
    html = owner.get("/m/ali-rahimov").text
    assert html.count('name="robots"') == 1 and 'content="noindex"' in html


def test_absolute_image_url_handles_both_storage_shapes() -> None:
    assert seo.absolute_url("/media/a/x.webp") == f"{SITE}/media/a/x.webp"
    assert seo.absolute_url("https://cdn.example/a/x.webp") == "https://cdn.example/a/x.webp"


def test_meta_ignores_the_request_host(client: TestClient) -> None:
    _make_master()
    html = client.get("/m/ali-rahimov", headers={"X-Forwarded-Host": "evil.example"}).text
    assert "evil.example" not in html and "testserver" not in _canonical(html)


# --- collection -------------------------------------------------------------------------------


def test_collection_meta(client: TestClient) -> None:
    _make_master()
    cid, _ = _add_collection("Кухня", photos=2)
    html = client.get(f"/m/ali-rahimov/c/{cid}").text
    assert _title(html).startswith("Кухня — Али Рахимов · ")
    assert "Плитка и фартук" in _description(html)
    assert _canonical(html) == f"{SITE}/m/ali-rahimov/c/{cid}"
    assert _og(html, "url") == f"{SITE}/m/ali-rahimov/c/{cid}"
    assert _og(html, "type") == "website"
    assert _og(html, "image").startswith(f"{SITE}/media/")
    assert _attr(html, r'<meta name="robots" content="([^"]*)">') == "index, follow"
    assert "hreflang" not in html.split("</head>")[0].replace('hreflang="ru"', "")


def test_draft_collection_keeps_noindex(client: TestClient) -> None:
    from tests.test_profile_web import _client

    _make_master(publish=False)
    cid, _ = _add_collection("Кухня")
    html = _client(client, "owner@example.com").get(f"/m/ali-rahimov/c/{cid}").text
    assert html.count('name="robots"') == 1 and 'content="noindex"' in html


# --- robots.txt and X-Robots-Tag --------------------------------------------------------------


def test_staging_robots_txt_disallows_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _app(monkeypatch, "staging").get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.split() == ["User-agent:", "*", "Disallow:", "/"]
    assert "Sitemap" not in r.text


def test_staging_sends_x_robots_tag_on_every_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_master()
    app = _app(monkeypatch, "staging")
    static = "/static/css/fonts.css"
    for path in (
        "/",
        "/m/ali-rahimov",
        "/m/nobody",
        "/no/such/page",
        "/media/a/" + "0" * 32 + ".webp",  # missing file
        static,
        "/health",
        "/robots.txt",
        "/sitemap.xml",
        "/signin",
        "/search?q=x",
        "/lang/tg?next=/",  # a redirect
    ):
        r = app.get(path)
        assert r.headers.get("x-robots-tag") == "noindex, nofollow", (path, r.status_code)
    assert app.get("/lang/tg?next=/").status_code in (302, 303, 307)
    assert app.get(static).status_code == 200


def test_development_and_test_also_send_x_robots_tag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/").headers["x-robots-tag"] == "noindex, nofollow"  # APP_ENV=test
    assert _app(monkeypatch, "development").get("/").headers["x-robots-tag"] == "noindex, nofollow"


def test_production_robots_txt_and_no_x_robots_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch, "production")
    r = app.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert lines[0] == "User-agent: *" and "Allow: /" in lines
    for path in (
        "/search",
        "/account",
        "/master/",
        "/auth/",
        "/signin",
        "/register",
        "/password/",
        "/confirm",
    ):
        assert f"Disallow: {path}" in lines
    assert "Disallow: /" not in lines
    assert "Disallow: /media/" not in lines  # media stays crawlable
    assert lines[-1] == f"Sitemap: {SITE}/sitemap.xml"
    for path in ("/", "/robots.txt", "/no/such/page", "/health"):
        assert "x-robots-tag" not in app.get(path).headers
