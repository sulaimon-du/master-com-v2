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


def test_forgot_password_over_the_address_limit_is_silent(client: TestClient, outbox) -> None:
    make_user()
    answers = [post(client, "/password/forgot", {"email": "ana@example.com"}) for _ in range(4)]
    assert [r.status_code for r in answers] == [200, 200, 200, 200]
    assert answers[3].text.split("<main")[1] == answers[0].text.split("<main")[1]
    assert len(outbox.sent) == 3


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


def test_using_one_reset_link_kills_the_older_ones(client: TestClient, outbox) -> None:
    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    older = link_from(outbox)
    post(client, "/password/forgot", {"email": "ana@example.com"})
    newer = link_from(outbox)
    assert older != newer
    assert (
        post(
            client, "/password/reset", {"token": newer, "password": "brand new pass 1"}
        ).status_code
        == 200
    )
    # The older email can no longer set a password.
    replay = post(client, "/password/reset", {"token": older, "password": "attacker pass 99"})
    assert replay.status_code == 400
    assert sign_in(client, password="brand new pass 1").status_code == 303


def test_reset_locks_the_user_before_the_link_so_a_google_handover_cannot_deadlock(
    client: TestClient, outbox
) -> None:
    """The Google handover locks the user row, then burns links. Reset must take the same order.

    Here a second transaction holds the user row (as the handover does) and then burns the
    link. If reset grabbed the link row first, that burn would wait for reset while reset waits
    for the user row: a deadlock, which Postgres resolves by failing one side with a 500.
    """
    import threading
    import time

    from app.services import auth

    make_user()
    post(client, "/password/forgot", {"email": "ana@example.com"})
    token = link_from(outbox)
    sessions = get_sessionmaker()
    holder = sessions()
    uid = holder.execute(
        text("SELECT id FROM users WHERE email='ana@example.com' FOR UPDATE")
    ).scalar_one()
    result: dict[str, object] = {}

    def run_reset() -> None:
        with sessions() as db:
            try:
                result["outcome"] = auth.reset_password(
                    db, ip="203.0.113.9", raw=token, password="a brand new pass"
                )
                db.commit()
            except Exception as exc:  # reported by the assertion below
                result["error"] = exc

    worker = threading.Thread(target=run_reset)
    worker.start()
    try:
        with sessions() as probe:
            for _ in range(200):
                waiting = probe.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity"
                        " WHERE wait_event_type = 'Lock' AND datname = current_database()"
                    )
                ).scalar_one()
                probe.rollback()
                if waiting:
                    break
                time.sleep(0.05)
        holder.execute(text("SET LOCAL lock_timeout = '2s'"))
        holder.execute(
            text("UPDATE auth_tokens SET used_at = now() WHERE user_id = :u AND used_at IS NULL"),
            {"u": uid},
        )
        holder.commit()
    finally:
        holder.close()
        worker.join(10)
    assert "error" not in result, result
    assert result["outcome"] is auth.Outcome.BAD_LINK
