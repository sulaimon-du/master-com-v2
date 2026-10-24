import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_sessionmaker
from tests.support import PASSWORD, link_from, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")


def test_forgot_password_sends_a_link_for_a_known_address(client: TestClient, outbox) -> None:
    make_user()
    r = post(client, "/password/forgot", {"email": "ana@example.com"})
    assert r.status_code == 200
    assert "Проверьте почту" in r.text
    assert [m.to for m in outbox.sent] == ["ana@example.com"]
    assert "/password/reset?token=" in outbox.sent[0].body


def test_forgot_password_looks_the_same_for_an_unknown_address(client: TestClient, outbox) -> None:
    known = post(client, "/password/forgot", {"email": "ana@example.com"})
    make_user()
    known_again = post(client, "/password/forgot", {"email": "ana@example.com"})
    unknown = post(client, "/password/forgot", {"email": "nobody@example.com"})
    assert unknown.status_code == known.status_code == known_again.status_code == 200
    assert unknown.text.split("<main")[1] == known_again.text.split("<main")[1]
    assert [m.to for m in outbox.sent] == ["ana@example.com"]


def test_reset_sets_a_new_password_and_ends_old_sessions(client: TestClient, outbox) -> None:
    make_user()
    old = TestClient(client.app, follow_redirects=False)
    sign_in(old)
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)

    r = post(client, "/password/reset", {"token": token, "password": "brand new pass 1"})
    assert r.status_code == 200
    assert "Пароль изменён" in r.text
    # A new password is a privilege change: the old session is dead.
    assert old.get("/account").status_code == 303
    assert sign_in(client, password=PASSWORD).status_code == 401
    assert sign_in(client, password="brand new pass 1").status_code == 303


def test_reset_link_works_once(client: TestClient, outbox) -> None:
    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)
    assert (
        post(
            client, "/password/reset", {"token": token, "password": "brand new pass 1"}
        ).status_code
        == 200
    )
    again = post(client, "/password/reset", {"token": token, "password": "another pass 22"})
    assert again.status_code == 400
    assert "Ссылка недействительна" in again.text


def test_expired_reset_link_is_refused(client: TestClient, outbox) -> None:
    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE auth_tokens SET expires_at = now() - interval '1 second'"))
        db.commit()
    r = post(client, "/password/reset", {"token": token, "password": "brand new pass 1"})
    assert r.status_code == 400
    assert sign_in(client, password=PASSWORD).status_code == 303


def test_reset_page_does_not_use_the_link(client: TestClient, outbox) -> None:
    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)
    client.get(f"/password/reset?token={token}")
    r = post(client, "/password/reset", {"token": token, "password": "brand new pass 1"})
    assert "Пароль изменён" in r.text


def test_reset_rejects_a_short_new_password(client: TestClient, outbox) -> None:
    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)
    r = post(client, "/password/reset", {"token": token, "password": "short"})
    assert r.status_code == 422
    # The link is still valid after a validation error.
    assert (
        post(
            client, "/password/reset", {"token": token, "password": "brand new pass 1"}
        ).status_code
        == 200
    )


def test_reset_does_not_work_for_a_blocked_account(client: TestClient, outbox) -> None:
    make_user(blocked=True)
    post(client, "/password/forgot", {"email": "ana@example.com"})
    assert outbox.sent == []
