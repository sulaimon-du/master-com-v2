import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.models.user import User
from app.services import auth
from tests.support import PASSWORD, make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")


def _session_cookie(client: TestClient) -> str:
    value = client.cookies.get("mc_session")
    assert value is not None
    return value


def test_sign_in_issues_a_new_id_even_when_an_old_cookie_is_present(client: TestClient) -> None:
    # Session fixation: an ID the browser already holds must not survive sign-in (R-011).
    make_user()
    sign_in(client)
    before = _session_cookie(client)
    sign_in(client)
    after = _session_cookie(client)
    assert before != after
    # The old ID is dead on the server.
    client.cookies.set("mc_session", before)
    assert client.get("/account").status_code == 303
    client.cookies.set("mc_session", after)
    assert client.get("/account").status_code == 200


def test_attacker_chosen_session_id_is_not_accepted(client: TestClient) -> None:
    make_user()
    client.cookies.set("mc_session", "A" * 43)
    assert client.get("/account").status_code == 303


def test_session_id_is_stored_only_as_a_hash(client: TestClient) -> None:
    make_user()
    sign_in(client)
    raw = _session_cookie(client)
    with get_sessionmaker()() as db:
        stored = db.execute(text("SELECT token_hash FROM auth_sessions")).scalars().all()
    assert len(stored) == 1
    assert raw not in stored


def test_idle_session_expires_after_thirty_days(client: TestClient) -> None:
    make_user()
    sign_in(client)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE auth_sessions SET last_seen_at = now() - interval '31 days'"))
        db.commit()
    assert client.get("/account").status_code == 303


def test_session_expires_after_ninety_days_even_if_active(client: TestClient) -> None:
    make_user()
    sign_in(client)
    with get_sessionmaker()() as db:
        db.execute(
            text(
                "UPDATE auth_sessions SET last_seen_at = now(), "
                "expires_at = now() - interval '1 minute'"
            )
        )
        db.commit()
    assert client.get("/account").status_code == 303


def test_sign_out_everywhere_ends_every_session_of_that_user(client: TestClient) -> None:
    make_user()
    other = TestClient(client.app, follow_redirects=False)
    sign_in(client)
    sign_in(other)
    post(client, "/account/sessions/revoke")
    assert client.get("/account").status_code == 303
    assert other.get("/account").status_code == 303


def test_sign_out_everywhere_leaves_other_users_alone(client: TestClient) -> None:
    make_user("ana@example.com")
    make_user("bo@example.com")
    ana = TestClient(client.app, follow_redirects=False)
    bo = TestClient(client.app, follow_redirects=False)
    sign_in(ana, "ana@example.com")
    sign_in(bo, "bo@example.com")
    post(ana, "/account/sessions/revoke")
    assert bo.get("/account").status_code == 200


def test_blocked_user_loses_every_session_at_once(client: TestClient) -> None:
    user_id = make_user()
    sign_in(client)
    with get_sessionmaker()() as db:
        user = db.get(User, user_id)
        assert user is not None
        auth.block_user(db, user)
        db.commit()
    assert client.get("/account").status_code == 303
    with get_sessionmaker()() as db:
        assert db.execute(text("SELECT count(*) FROM auth_sessions")).scalar_one() == 0


def test_blocked_user_session_is_refused_even_if_the_row_survived(client: TestClient) -> None:
    user_id = make_user()
    sign_in(client)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE users SET blocked_at = now() WHERE id = :id"), {"id": user_id})
        db.commit()
    assert client.get("/account").status_code == 303


def test_password_sign_in_still_works_after_logout(client: TestClient) -> None:
    make_user()
    sign_in(client)
    post(client, "/signout")
    assert sign_in(client, password=PASSWORD).status_code == 303
