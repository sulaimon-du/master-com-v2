import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_sessionmaker
from tests.support import PASSWORD, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")


def test_confirmed_user_signs_in_with_a_hardened_cookie(client: TestClient) -> None:
    make_user()
    r = sign_in(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/account"
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("mc_session="))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert client.get("/account").status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_message(client: TestClient) -> None:
    make_user()
    wrong = sign_in(client, password="wrong horse 9")
    unknown = sign_in(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert "Неверная почта или пароль." in wrong.text
    assert "Неверная почта или пароль." in unknown.text
    assert "mc_session" not in wrong.headers.get("set-cookie", "")


def test_unconfirmed_account_is_told_to_confirm_only_after_the_right_password(
    client: TestClient,
) -> None:
    make_user(confirmed=False)
    wrong = sign_in(client, password="wrong horse 9")
    assert wrong.status_code == 401
    assert "Сначала подтвердите почту" not in wrong.text
    right = sign_in(client)
    assert right.status_code == 403
    assert "Сначала подтвердите почту" in right.text
    assert "mc_session" not in right.headers.get("set-cookie", "")


def test_password_sign_in_is_rate_limited_per_email(client: TestClient) -> None:
    make_user()
    for _ in range(5):
        assert sign_in(client, password="wrong horse 9").status_code == 401
    # The sixth attempt is refused even with the right password.
    blocked = sign_in(client)
    assert blocked.status_code == 429
    assert "Слишком много попыток" in blocked.text
    assert "mc_session" not in blocked.headers.get("set-cookie", "")


def test_rate_limit_window_reopens_after_fifteen_minutes(client: TestClient) -> None:
    make_user()
    for _ in range(6):
        sign_in(client, password="wrong horse 9")
    with get_sessionmaker()() as db:
        db.execute(
            text("UPDATE rate_limits SET window_start = window_start - interval '16 minutes'")
        )
        db.commit()
    assert sign_in(client).status_code == 303


def test_blocked_account_cannot_sign_in_and_gets_no_hint(client: TestClient) -> None:
    make_user(blocked=True)
    r = sign_in(client)
    assert r.status_code == 401
    assert "Неверная почта или пароль." in r.text


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example"])
def test_next_parameter_cannot_leave_the_site(client: TestClient, target: str) -> None:
    make_user()
    r = post(client, "/signin", {"email": "ana@example.com", "password": PASSWORD, "next": target})
    assert r.headers["location"] == "/account"


def test_sign_out_ends_the_session_on_the_server(client: TestClient) -> None:
    make_user()
    sign_in(client)
    assert client.get("/account").status_code == 200
    out = post(client, "/signout")
    assert out.status_code == 303
    with get_sessionmaker()() as db:
        assert db.execute(text("SELECT count(*) FROM auth_sessions")).scalar_one() == 0
    assert client.get("/account").status_code == 303


def test_client_ip_uses_cloudflare_header_only_behind_the_proxy(monkeypatch) -> None:
    from starlette.requests import Request

    from app.core.config import get_settings
    from app.web.deps import client_ip

    def request(headers: list[tuple[bytes, bytes]]) -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": ("10.0.0.1", 1234),
        }
        return Request(scope)

    spoofed = [
        (b"x-forwarded-for", b"6.6.6.6, 203.0.113.9"),
        (b"cf-connecting-ip", b"198.51.100.7"),
    ]
    # Development has no proxy: headers are ignored, the connection address is used.
    assert client_ip(request(spoofed)) == "10.0.0.1"

    monkeypatch.setenv("APP_ENV", "staging")
    get_settings.cache_clear()
    try:
        # Behind Cloudflare: its header wins, X-Forwarded-For is never read.
        assert client_ip(request(spoofed)) == "198.51.100.7"
        assert client_ip(request([(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")])) == "10.0.0.1"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
