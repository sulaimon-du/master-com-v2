import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.services import google_oidc
from tests.support import make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture(scope="module")
def signing_key():  # type: ignore[no-untyped-def]
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def __init__(self, public_key) -> None:  # type: ignore[no-untyped-def]
        self.key = public_key

    def get_signing_key_from_jwt(self, token: str):  # type: ignore[no-untyped-def]
        return type("Key", (), {"key": self.key})()


@pytest.fixture
def google(monkeypatch, signing_key, settings):  # type: ignore[no-untyped-def]
    """Google sign-in on, with Google's keys and token endpoint replaced by test doubles."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(google_oidc, "jwk_client", FakeJWKS(signing_key.public_key()))
    state = {"id_token": None}

    def fake_exchange(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["client_secret"] == "test-google-secret"
        return state["id_token"]

    monkeypatch.setattr(google_oidc, "exchange_code", fake_exchange)
    yield state
    get_settings.cache_clear()


def _id_token(signing_key, nonce: str, **overrides) -> str:  # type: ignore[no-untyped-def]
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "ana@gmail.com",
        "email_verified": True,
        "name": "Ана",
        "nonce": nonce,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test"})


def _start(client: TestClient) -> tuple[str, str]:
    r = client.get("/auth/google/start")
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    state = client.cookies.get("mc_oauth")
    assert state is not None
    return state, location


def _nonce_of(client: TestClient) -> str:
    with get_sessionmaker()() as db:
        return db.execute(
            text("SELECT nonce FROM oauth_flows ORDER BY id DESC LIMIT 1")
        ).scalar_one()


def test_google_button_is_hidden_when_not_configured(client: TestClient) -> None:
    r = client.get("/signin")
    assert "/auth/google/start" not in r.text
    assert client.get("/auth/google/start").status_code == 404


def test_google_button_appears_when_configured(client: TestClient, google) -> None:  # type: ignore[no-untyped-def]
    assert "/auth/google/start" in client.get("/signin").text


def test_start_uses_state_nonce_and_pkce(client: TestClient, google) -> None:  # type: ignore[no-untyped-def]
    state, location = _start(client)
    assert "state=" + state in location
    assert "code_challenge_method=S256" in location
    assert "nonce=" in location
    assert "prompt=select_account" in location


def test_sign_in_with_a_verified_google_account_creates_a_confirmed_user(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    r = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 303
    assert r.headers["location"] == "/account"
    assert client.get("/account").status_code == 200
    with get_sessionmaker()() as db:
        row = db.execute(text("SELECT email, google_sub, email_verified_at FROM users")).one()
        assert row.email == "ana@gmail.com"
        assert row.google_sub == "google-sub-1"
        assert row.email_verified_at is not None


def test_callback_with_a_different_state_is_refused(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    r = client.get("/auth/google/callback?state=forged&code=abc")
    assert r.status_code == 400
    assert "Не удалось войти через Google" in r.text
    assert "mc_session" not in r.headers.get("set-cookie", "")


def test_callback_cannot_be_replayed(client: TestClient, google, signing_key) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 303
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 400


def test_wrong_nonce_is_refused(client: TestClient, google, signing_key) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, "not-the-nonce")
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 400


def test_wrong_audience_is_refused(client: TestClient, google, signing_key) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client), aud="someone-else")
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 400


def test_unverified_google_email_is_refused(client: TestClient, google, signing_key) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client), email_verified=False)
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 400


def test_expired_id_token_is_refused(client: TestClient, google, signing_key) -> None:  # type: ignore[no-untyped-def]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client), exp=int(time.time()) - 60)
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 400


def test_google_email_matching_a_password_account_is_not_merged(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    make_user("ana@gmail.com")
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    r = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 409
    assert "Аккаунт с этой почтой уже есть" in r.text
    assert "mc_session" not in r.headers.get("set-cookie", "")
    with get_sessionmaker()() as db:
        assert (
            db.execute(text("SELECT count(*) FROM users WHERE google_sub IS NOT NULL")).scalar_one()
            == 0
        )


def test_linking_from_settings_attaches_google_to_the_signed_in_user(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    make_user("ana@example.com")
    sign_in(client)
    r = post(client, "/account/google/link")
    assert r.status_code == 303
    state = client.cookies.get("mc_oauth")
    google["id_token"] = _id_token(signing_key, _nonce_of(client), email="other@gmail.com")
    done = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert done.headers["location"] == "/account?notice=linked"
    with get_sessionmaker()() as db:
        row = db.execute(text("SELECT email, google_sub FROM users")).one()
        assert row.email == "ana@example.com"
        assert row.google_sub == "google-sub-1"


def test_link_start_needs_csrf_so_another_site_cannot_start_it(client: TestClient, google) -> None:  # type: ignore[no-untyped-def]
    make_user()
    sign_in(client)
    r = client.post("/account/google/link")
    assert r.status_code == 403


def test_link_start_needs_a_signed_in_user(client: TestClient, google) -> None:  # type: ignore[no-untyped-def]
    make_user()
    r = post(client, "/account/google/link")
    assert r.headers["location"] == "/signin?next=/account"


def test_linking_a_google_account_used_elsewhere_is_refused(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    make_user("ana@example.com")
    make_user("bo@example.com", google_sub="google-sub-1")
    sign_in(client)
    post(client, "/account/google/link")
    state = client.cookies.get("mc_oauth")
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    done = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert done.headers["location"] == "/account?notice=taken"
    with get_sessionmaker()() as db:
        assert (
            db.execute(
                text("SELECT google_sub FROM users WHERE email='ana@example.com'")
            ).scalar_one()
            is None
        )


def test_link_callback_for_a_different_signed_in_user_is_refused(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    make_user("ana@example.com")
    make_user("bo@example.com")
    sign_in(client)
    post(client, "/account/google/link")
    state = client.cookies.get("mc_oauth")
    # The browser signs in as someone else before the callback arrives.
    client.cookies.clear()
    sign_in(client, "bo@example.com")
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    client.cookies.set("mc_oauth", state)
    r = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 403
    with get_sessionmaker()() as db:
        assert (
            db.execute(text("SELECT count(*) FROM users WHERE google_sub IS NOT NULL")).scalar_one()
            == 0
        )


def test_google_owner_takes_over_an_unconfirmed_squatted_account(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    # A stranger registered ana@gmail.com with their own password but never confirmed it.
    make_user("ana@gmail.com", password="stranger pass 1", confirmed=False, name="Stranger")
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    r = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 303
    assert r.headers["location"] == "/account"
    with get_sessionmaker()() as db:
        row = db.execute(
            text("SELECT google_sub, password_hash, email_verified_at, name FROM users")
        ).one()
    assert row.google_sub == "google-sub-1"
    assert row.password_hash is None
    assert row.email_verified_at is not None
    assert row.name == "Ана"
    stranger = TestClient(client.app, follow_redirects=False)
    assert sign_in(stranger, "ana@gmail.com", "stranger pass 1").status_code == 401


def test_google_start_is_rate_limited(client: TestClient, google) -> None:  # type: ignore[no-untyped-def]
    for _ in range(30):
        assert client.get("/auth/google/start").status_code == 303
    assert client.get("/auth/google/start").status_code == 429


def test_takeover_burns_the_squatters_confirmation_link(
    client: TestClient, google, signing_key, outbox
) -> None:  # type: ignore[no-untyped-def]
    post(
        client, "/register", {"name": "S", "email": "ana@gmail.com", "password": "stranger pass 1"}
    )
    old_link = outbox.sent[-1].body.split("token=")[1].split()[0]
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 303
    other = TestClient(client.app, follow_redirects=False)
    r = post(other, "/confirm", {"token": old_link, "password": "stranger pass 1"})
    assert r.status_code == 400


def test_blocked_unconfirmed_account_is_not_taken_over(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    make_user("ana@gmail.com", confirmed=False, blocked=True)
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    r = client.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 403
    with get_sessionmaker()() as db:
        assert db.execute(text("SELECT google_sub FROM users")).scalar_one() is None


def test_unconfirmed_account_with_another_proven_method_is_not_taken_over(
    client: TestClient, google, signing_key
) -> None:  # type: ignore[no-untyped-def]
    # A Telegram account (week 4) that added an email it has not confirmed yet still belongs
    # to its Telegram owner.
    user_id = make_user("ana@gmail.com", confirmed=False)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE users SET telegram_id = 42 WHERE id = :id"), {"id": user_id})
        db.commit()
    state, _ = _start(client)
    google["id_token"] = _id_token(signing_key, _nonce_of(client))
    assert client.get(f"/auth/google/callback?state={state}&code=abc").status_code == 409
