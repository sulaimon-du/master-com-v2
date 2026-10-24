import re

import pytest
from fastapi.testclient import TestClient

from tests.support import make_user, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

PAGES = ["/signin", "/register", "/password/forgot", "/confirm/resend", "/password/reset"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_in_russian_by_default(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert '<html lang="ru">' in r.text
    assert "/static/css/app.css" in r.text
    assert "Перейти к содержимому" in r.text


def test_tajik_is_used_when_the_language_cookie_says_so(client: TestClient) -> None:
    client.cookies.set("mc_lang", "tg")
    r = client.get("/signin")
    assert '<html lang="tg">' in r.text
    assert "Ворид шудан" in r.text


def test_language_switch_sets_the_cookie(client: TestClient) -> None:
    r = client.get("/lang/tg")
    assert r.status_code == 303
    assert "mc_lang=tg" in r.headers["set-cookie"]
    assert client.get("/lang/xx").headers["set-cookie"].startswith("mc_lang=ru")


@pytest.mark.parametrize("path", [*PAGES, "/account"])
def test_pages_have_no_inline_script_or_style(client: TestClient, path: str) -> None:
    make_user()
    sign_in(client)
    html = client.get(path).text
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "style=" not in html
    assert "<style" not in html


def test_forms_carry_the_csrf_token(client: TestClient) -> None:
    html = client.get("/signin").text
    token = client.cookies.get("mc_csrf")
    assert token
    assert f'name="csrf_token" value="{token}"' in html


def test_user_text_is_escaped(client: TestClient) -> None:
    make_user("ana@example.com", name="<script>alert(1)</script>")
    sign_in(client)
    html = client.get("/account").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pages_render_in_csp_safe_form_on_mobile_width(client: TestClient) -> None:
    html = client.get("/register").text
    assert 'name="viewport" content="width=device-width' in html


@pytest.mark.parametrize("path", ["/signin", "/register", "/account"])
def test_pages_are_never_cached(client: TestClient, path: str) -> None:
    make_user()
    sign_in(client)
    assert client.get(path).headers["cache-control"] == "no-store"


def test_sign_in_redirect_is_never_cached(client: TestClient) -> None:
    make_user()
    assert sign_in(client).headers["cache-control"] == "no-store"


def test_language_switch_returns_to_the_same_page(client: TestClient) -> None:
    assert client.get("/lang/tg?next=/register").headers["location"] == "/register"
    assert client.get("/lang/tg?next=//evil.example").headers["location"] == "/"
    assert client.get("/lang/tg?next=https://evil.example").headers["location"] == "/"
    html = client.get("/register").text
    assert "/lang/ru?next=/register" in html


def test_templates_get_a_view_without_sensitive_fields(client: TestClient) -> None:
    from app.schemas.account import AccountView

    make_user()
    sign_in(client)
    r = client.get("/account")
    view = r.context["user"]  # type: ignore[attr-defined]
    assert isinstance(view, AccountView)
    assert not hasattr(view, "password_hash")
    assert not hasattr(view, "google_sub")
    assert view.has_password and view.email_confirmed and not view.google_linked
