import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.models.auth import AuthToken
from app.models.user import User
from tests.support import PASSWORD, link_from, make_user, post, sign_in

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


def test_confirmation_link_works_once_and_signs_the_owner_in(client: TestClient, outbox) -> None:
    _register(client)
    token = link_from(outbox)
    assert client.get(f"/confirm?token={token}").status_code == 200
    first = post(client, "/confirm", {"token": token, "password": PASSWORD})
    assert first.status_code == 303
    assert first.headers["location"] == "/account?notice=confirmed"
    assert client.get("/account").status_code == 200
    again = post(client, "/confirm", {"token": token, "password": PASSWORD})
    assert again.status_code == 400


def test_confirmation_needs_the_password_and_a_wrong_one_keeps_the_link(
    client: TestClient, outbox
) -> None:
    _register(client)
    token = link_from(outbox)
    wrong = post(client, "/confirm", {"token": token, "password": "not my pass 1"})
    assert wrong.status_code == 401
    assert "Пароль не подходит" in wrong.text
    assert "mc_session" not in wrong.headers.get("set-cookie", "")
    assert post(client, "/confirm", {"token": token, "password": PASSWORD}).status_code == 303


def test_opening_the_confirm_page_does_not_use_the_link(client: TestClient, outbox) -> None:
    # Mail scanners open links with GET; only the POST form may use up the token.
    _register(client)
    token = link_from(outbox)
    client.get(f"/confirm?token={token}")
    client.get(f"/confirm?token={token}")
    assert post(client, "/confirm", {"token": token, "password": PASSWORD}).status_code == 303


def test_stranger_cannot_pre_register_someone_elses_address(client: TestClient, outbox) -> None:
    # Pre-hijack: a stranger registers the victim's address with their own password and waits
    # for the victim to confirm it from the inbox.
    stranger = TestClient(client.app, follow_redirects=False)
    post(
        stranger,
        "/register",
        {"name": "S", "email": "ana@example.com", "password": "stranger pass 1"},
    )
    # The victim registers too, gets the "account exists" email, asks for a new link...
    _register(client)
    post(client, "/confirm/resend", {"email": "ana@example.com"})
    token = link_from(outbox)
    # ...and cannot confirm with their own password: the account is not theirs yet.
    assert post(client, "/confirm", {"token": token, "password": PASSWORD}).status_code == 401
    assert sign_in(stranger, password="stranger pass 1").status_code == 401
    # Resetting the password proves the inbox, replaces the stranger's password and confirms.
    post(client, "/password/forgot", {"email": "ana@example.com"})
    reset = post(client, "/password/reset", {"token": link_from(outbox), "password": PASSWORD})
    assert reset.status_code == 200
    assert sign_in(stranger, password="stranger pass 1").status_code == 401
    assert sign_in(client).status_code == 303


def test_expired_confirmation_link_is_refused(client: TestClient, outbox) -> None:
    _register(client)
    token = link_from(outbox)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE auth_tokens SET expires_at = now() - interval '1 minute'"))
        db.commit()
    r = post(client, "/confirm", {"token": token, "password": PASSWORD})
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


def test_registration_emails_to_one_address_are_capped(client: TestClient, outbox) -> None:
    make_user("ana@example.com")
    pages = [_register(client) for _ in range(5)]
    # Same answer every time, but the inbox gets at most three reminders an hour.
    assert {p.status_code for p in pages} == {200}
    assert len({p.text.split("<main")[1].split("</main>")[0] for p in pages}) == 1
    assert len(outbox.sent) == 3


def test_confirming_kills_the_older_confirmation_links(client: TestClient, outbox) -> None:
    make_user("ana@example.com", confirmed=False)
    post(client, "/confirm/resend", {"email": "ana@example.com"})
    older = link_from(outbox)
    post(client, "/confirm/resend", {"email": "ana@example.com"})
    newer = link_from(outbox)
    assert post(client, "/confirm", {"token": newer, "password": PASSWORD}).status_code == 303
    assert post(client, "/confirm", {"token": older, "password": PASSWORD}).status_code == 400


def test_confirm_attempts_are_limited_per_ip(client: TestClient) -> None:
    for _ in range(30):
        assert post(client, "/confirm", {"token": "bogus", "password": PASSWORD}).status_code == 400
    assert post(client, "/confirm", {"token": "bogus", "password": PASSWORD}).status_code == 429


def test_confirm_link_shares_the_sign_in_limit_of_the_address(client: TestClient, outbox) -> None:
    # A leaked link must not allow more password guesses than the sign-in form does.
    _register(client)
    token = link_from(outbox)
    for _ in range(5):
        assert (
            post(client, "/confirm", {"token": token, "password": "guess pass 01"}).status_code
            == 401
        )
    assert post(client, "/confirm", {"token": token, "password": PASSWORD}).status_code == 429


def test_reset_burns_open_confirmation_links(client: TestClient, outbox) -> None:
    _register(client)
    confirm_token = link_from(outbox)
    post(client, "/password/forgot", {"email": "ana@example.com"})
    post(client, "/password/reset", {"token": link_from(outbox), "password": "brand new pass 1"})
    r = post(client, "/confirm", {"token": confirm_token, "password": "brand new pass 1"})
    assert r.status_code == 400
