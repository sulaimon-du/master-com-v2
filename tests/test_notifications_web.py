"""The notification settings page (spec D8, D10.6): own switches only, CSRF, bot-blocked notice."""

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models.jobs import CHANNELS, EVENTS
from app.models.user import User
from app.services import notifications
from tests.support import make_user, post, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

PAGE = "/account/notifications"


def _prefs(user_id: int) -> dict[tuple[str, str], bool]:
    with get_sessionmaker()() as db:
        return notifications.prefs(db, user_id)


def _set(user_id: int, **values: object) -> None:
    with get_sessionmaker()() as db:
        user = db.get(User, user_id)
        for key, value in values.items():
            setattr(user, key, value)
        db.commit()


def test_guests_are_sent_to_sign_in(client: TestClient) -> None:
    assert client.get(PAGE).headers["location"] == f"/signin?next={PAGE}"
    r = post(client, PAGE, {"on": "chosen:email"})
    assert r.status_code == 303 and r.headers["location"] == f"/signin?next={PAGE}"


def test_the_page_lists_every_switch_on_by_default(client: TestClient) -> None:
    make_user()
    sign_in(client)
    r = client.get(PAGE)
    assert r.status_code == 200
    for e in EVENTS:
        for c in CHANNELS:
            assert f'value="{e}:{c}" ' in r.text
    assert r.text.count(" checked>") == len(EVENTS) * len(CHANNELS)
    assert 'role="alert"' not in r.text


def test_saving_keeps_the_checked_switches_and_turns_the_rest_off(client: TestClient) -> None:
    uid = make_user()
    sign_in(client)
    r = client.post(
        PAGE,
        data={
            "on": ["chosen:email", "new_response:telegram", "bogus:email"],
            "csrf_token": client.cookies.get("mc_csrf"),
        },
    )
    assert r.status_code == 303 and r.headers["location"] == f"{PAGE}?notice=saved"
    on = {k for k, v in _prefs(uid).items() if v}
    assert on == {("chosen", "email"), ("new_response", "telegram")}
    page = client.get(f"{PAGE}?notice=saved").text
    assert page.count(" checked>") == 2


def test_someone_else_cannot_change_my_switches(client: TestClient) -> None:
    ana = make_user()
    bob = make_user("bob@example.com")
    sign_in(client, "bob@example.com")
    # A smuggled user id is ignored: there is no way to name another account.
    r = post(client, PAGE, {"user_id": str(ana), "on": "chosen:email"})
    assert r.status_code == 303
    assert all(_prefs(ana).values())
    assert [k for k, v in _prefs(bob).items() if v] == [("chosen", "email")]
    r = post(client, f"{PAGE}?user_id={ana}", {})
    assert all(_prefs(ana).values())


def test_saving_needs_the_csrf_token(client: TestClient) -> None:
    uid = make_user()
    sign_in(client)
    r = client.post(PAGE, data={"on": "chosen:email"})
    assert r.status_code == 403
    r = client.post(PAGE, data={"on": "chosen:email", "csrf_token": "forged"})
    assert r.status_code == 403
    assert all(_prefs(uid).values())


def test_a_blocked_bot_is_shown_on_the_page_and_the_account(client: TestClient) -> None:
    uid = make_user()
    _set(uid, telegram_id=555001, telegram_blocked_at=utcnow())
    sign_in(client)
    for path in (PAGE, "/account"):
        text = client.get(path).text
        assert 'role="alert"' in text and "Старт" in text
    assert f'href="{PAGE}"' in client.get("/account").text


def test_unavailable_channels_are_explained(client: TestClient) -> None:
    uid = make_user()
    sign_in(client)
    _set(uid, email_verified_at=None)
    text = client.get(PAGE).text
    assert "Telegram не привязан" in text and "Почта не подтверждена" in text


def test_every_switch_label_is_translated() -> None:
    from app.core.i18n import translate
    from app.web.notifications import CHANNEL_LABELS, EVENT_LABELS

    assert EVENT_LABELS.keys() == set(EVENTS) and CHANNEL_LABELS.keys() == set(CHANNELS)
    for key in (*EVENT_LABELS.values(), *CHANNEL_LABELS.values()):
        for lang in ("ru", "tg"):
            assert translate(lang, key) != key
