import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.models.auth import AuthToken
from app.models.user import User
from tests.support import PASSWORD, csrf_token, link_from, make_user, post

pytestmark = pytest.mark.usefixtures("clean_auth")


def _register(client: TestClient, email: str = "ana@example.com", password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return post(client, "/register", {"name": "Ана", "email": email, "password": password})


def test_register_creates_an_unconfirmed_account_and_sends_a_link(
    client: TestClient, outbox
) -> None:
    r = _register(client)
    assert r.status_code == 200
    assert "Проверьте почту" in r.text
    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(User.email == "ana@example.com"))
        assert user is not None
        assert user.email_verified_at is None
        assert user.password_hash.startswith("$argon2id$")
    assert len(outbox.sent) == 1
    assert outbox.sent[0].to == "ana@example.com"
    assert "/confirm?token=" in outbox.sent[0].body


def test_registering_an_existing_address_looks_the_same_and_creates_nothing(
    client: TestClient, outbox
) -> None:
    make_user("ana@example.com")
    first = _register(client)
    fresh = _register(client, email="new@example.com")
    assert first.status_code == fresh.status_code == 200
    assert (
        first.text.split("<main")[1].split("</main>")[0]
        == fresh.text.split("<main")[1].split("</main>")[0]
    )
    # The existing owner gets a reminder; nobody gets a new account.
    assert [m.to for m in outbox.sent] == ["ana@example.com", "new@example.com"]
    assert "/confirm?token=" not in outbox.sent[0].body


def test_weak_password_and_bad_email_are_refused(client: TestClient, outbox) -> None:
    short = _register(client, password="123456789")
    assert short.status_code == 422
    assert "от 10 до 128" in short.text
    bad = _register(client, email="not-an-email")
    assert bad.status_code == 422
    assert outbox.sent == []


def test_confirmation_link_works_once(client: TestClient, outbox) -> None:
    _register(client)
    token = link_from(outbox)
    assert client.get(f"/confirm?token={token}").status_code == 200
    first = client.post("/confirm", data={"token": token, "csrf_token": csrf_token(client)})
    assert first.status_code == 200
    assert "Почта подтверждена" in first.text
    again = client.post("/confirm", data={"token": token, "csrf_token": csrf_token(client)})
    assert again.status_code == 400


def test_opening_the_confirm_page_does_not_use_the_link(client: TestClient, outbox) -> None:
    # Mail scanners open links with GET; only the POST button may use up the token.
    _register(client)
    token = link_from(outbox)
    client.get(f"/confirm?token={token}")
    client.get(f"/confirm?token={token}")
    r = post(client, "/confirm", {"token": token})
    assert "Почта подтверждена" in r.text


def test_expired_confirmation_link_is_refused(client: TestClient, outbox) -> None:
    _register(client)
    token = link_from(outbox)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE auth_tokens SET expires_at = now() - interval '1 minute'"))
        db.commit()
    r = post(client, "/confirm", {"token": token})
    assert r.status_code == 400


def test_stored_token_is_a_hash_not_the_link_secret(client: TestClient, outbox) -> None:
    _register(client)
    token = link_from(outbox)
    with get_sessionmaker()() as db:
        stored = db.scalars(select(AuthToken.token_hash)).all()
    assert token not in stored
    assert len(stored) == 1 and len(stored[0]) == 64


def test_resend_gives_the_same_answer_for_unknown_addresses(client: TestClient, outbox) -> None:
    unknown = post(client, "/confirm/resend", {"email": "nobody@example.com"})
    assert unknown.status_code == 200
    assert "Новое письмо для подтверждения" in unknown.text
    assert outbox.sent == []


def test_resend_sends_a_new_link_to_an_unconfirmed_account(client: TestClient, outbox) -> None:
    make_user("ana@example.com", confirmed=False)
    post(client, "/confirm/resend", {"email": "ana@example.com"})
    assert [m.to for m in outbox.sent] == ["ana@example.com"]
