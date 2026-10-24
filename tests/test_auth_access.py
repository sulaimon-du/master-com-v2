import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.i18n import LANGS, translate
from tests.support import make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
LOCALES = Path(__file__).resolve().parent.parent / "app" / "locales"


def test_guest_is_sent_to_sign_in_from_settings(client: TestClient) -> None:
    r = client.get("/account")
    assert r.status_code == 303
    assert r.headers["location"] == "/signin?next=/account"


def test_account_page_shows_only_the_signed_in_user(client: TestClient) -> None:
    make_user("ana@example.com", name="Ана")
    make_user("bo@example.com", name="Бобо")
    sign_in(client, "ana@example.com")
    page = client.get("/account").text
    assert "ana@example.com" in page
    assert "bo@example.com" not in page
    assert "Бобо" not in page


def test_user_cannot_revoke_another_users_sessions(client: TestClient) -> None:
    make_user("ana@example.com")
    make_user("bo@example.com")
    ana = TestClient(client.app, follow_redirects=False)
    bo = TestClient(client.app, follow_redirects=False)
    sign_in(ana, "ana@example.com")
    sign_in(bo, "bo@example.com")
    # Ana's request ends only Ana's sessions; there is no parameter that names another user.
    post(ana, "/account/sessions/revoke?user_id=2")
    assert bo.get("/account").status_code == 200


def test_signed_out_account_actions_do_nothing(client: TestClient) -> None:
    make_user()
    r = post(client, "/account/sessions/revoke")
    assert r.headers["location"] == "/signin?next=/account"


def test_ru_and_tg_catalogs_have_the_same_non_empty_keys() -> None:
    ru = _catalog("ru")
    tg = _catalog("tg")
    assert ru.keys() == tg.keys()
    for key in ru:
        assert tg[key], f"empty Tajik text for {key}"


def test_templates_only_use_known_translation_keys() -> None:
    known = _catalog("ru")
    pattern = re.compile(r"""\bt\(\s*['"]([a-z_.]+)['"]""")
    used: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    # Keys chosen in Python (notices, errors) are checked in the page tests.
    missing = sorted(key for key in used if key not in known)
    assert not missing, f"keys used in templates but not translated: {missing}"


def _catalog(lang: str) -> dict[str, str]:
    import json

    assert lang in LANGS
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def test_translate_falls_back_to_russian_when_tajik_is_missing() -> None:
    assert translate("tg", "app.name") == "Master COM"
    assert translate("tg", "no.such.key") == "no.such.key"
