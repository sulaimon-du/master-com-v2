"""Feed, "My responses", the master's request page, respond/withdraw/choose/complete (Task 6).

Routes commit for real, so data is made in committed sessions and wiped by `clean_auth`
(TRUNCATE users CASCADE takes requests and responses with it).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import JobRequest, JobResponse, MasterProfile, User
from app.schemas.requests import RequestInput
from app.services import masters
from app.services import requests as request_svc
from app.services.masters import BasicsInput
from tests.feed_support import city_without_districts, spec
from tests.support import csrf_token, make_user, sign_in

pytestmark = pytest.mark.usefixtures("clean_auth")

ADDRESS = "ул. Секретная 42, кв. 7"
DESCRIPTION = "Нужно заменить смеситель на кухне и проверить трубы"
CLIENT_PHONE = "+992901112233"
MASTER_TG = "ali_santekhnik"


# --- helpers ----------------------------------------------------------------------------------


def _browser(client: TestClient, email: str, **kw: object) -> tuple[TestClient, int]:
    browser = TestClient(client.app, follow_redirects=False)
    uid = make_user(email, **kw)  # type: ignore[arg-type]
    assert sign_in(browser, email).status_code == 303
    return browser, uid


def _post(browser: TestClient, url: str, data: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return browser.post(url, data={**(data or {}), "csrf_token": csrf_token(browser)})


def _make_master(uid: int, *, specs: tuple[str, ...] = ("santekhnik",), ready: bool = True) -> int:
    """Give the user a master profile; published (visible in the feed) when `ready`."""
    with get_sessionmaker()() as db:
        owner = db.get(User, uid)
        owner.phone = "+99290" + str(uid).rjust(7, "0")
        owner.phone_verified_at = utcnow()
        db.flush()
        n = uuid.uuid4().hex[:8]
        profile = masters.create_profile(
            db,
            owner,
            BasicsInput(
                display_name=f"Мастер {uid}",
                slug=f"m-{n}",
                headline=None,
                experience_years=3,
                is_available=True,
            ),
        )
        if ready:
            ids = [spec(db, s).id for s in specs]
            masters.set_specialties(db, profile, ids, ids[:1])
            masters.set_areas(db, profile, [city_without_districts(db).id], [])
            masters.set_contacts(db, profile, telegram=MASTER_TG, whatsapp=None, instagram=None)
        assert (profile.published_at is not None) == ready
        db.commit()
        return profile.id


def _make_request(client_uid: int, *, specs: tuple[str, ...] = ("santekhnik",)) -> int:
    with get_sessionmaker()() as db:
        user = db.get(User, client_uid)
        created = request_svc.create_request(
            db,
            user,
            RequestInput(
                specialty_ids=[spec(db, s).id for s in specs],
                city_id=city_without_districts(db).id,
                description=DESCRIPTION,
                timing="flexible",
                address=ADDRESS,
            ),
        )
        db.commit()
        return created.id


def _update(model: type, row_id: int, **values: object) -> None:
    with get_sessionmaker()() as db:
        row = db.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def _row(request_id: int) -> JobRequest:
    with get_sessionmaker()() as db:
        return db.get(JobRequest, request_id)


def _response_id(request_id: int, profile_id: int) -> int:
    with get_sessionmaker()() as db:
        return db.scalar(
            select(JobResponse.id).where(
                JobResponse.request_id == request_id, JobResponse.master_id == profile_id
            )
        )


@pytest.fixture
def scene(client: TestClient) -> dict[str, object]:
    """A client with a verified phone and a request; one eligible master; a plain user."""
    ana, ana_id = _browser(client, "ana@example.com", name="Нигина")
    _update(User, ana_id, phone=CLIENT_PHONE, phone_verified_at=utcnow())
    ali, ali_id = _browser(client, "ali@example.com")
    ali_profile = _make_master(ali_id)
    plain, _ = _browser(client, "plain@example.com")
    request_id = _make_request(ana_id)
    return {
        "ana": ana,
        "ana_id": ana_id,
        "ali": ali,
        "ali_id": ali_id,
        "ali_profile": ali_profile,
        "plain": plain,
        "rid": request_id,
    }


def _respond(browser: TestClient, request_id: int, **data: str):  # type: ignore[no-untyped-def]
    return _post(browser, f"/requests/{request_id}/respond", data)


# --- guests -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/feed", "/responses/mine"])
def test_guest_is_sent_to_signin(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 303 and r.headers["location"] == f"/signin?next={path}"


@pytest.mark.parametrize("action", ["respond", "withdraw", "choose", "complete"])
def test_guest_post_needs_csrf_then_signin(client: TestClient, action: str) -> None:
    assert client.post(f"/requests/1/{action}", data={}).status_code == 403
    r = _post(client, f"/requests/1/{action}")
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")


def test_guests_never_see_masters_contacts(client: TestClient, scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    r = client.get(f"/requests/{rid}")
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")
    assert MASTER_TG not in r.text and DESCRIPTION not in r.text


# --- the feed ---------------------------------------------------------------------------------


def test_feed_lists_matching_requests_for_an_eligible_master(scene: dict) -> None:
    r = scene["ali"].get("/feed")
    assert r.status_code == 200
    assert DESCRIPTION in r.text and f'href="/requests/{scene["rid"]}"' in r.text
    assert ADDRESS not in r.text and CLIENT_PHONE not in r.text


def test_feed_is_empty_for_users_who_are_not_visible_masters(
    client: TestClient, scene: dict
) -> None:
    boris, boris_id = _browser(client, "boris@example.com")
    _make_master(boris_id, ready=False)  # unpublished
    for browser in (scene["plain"], boris, scene["ana"]):
        r = browser.get("/feed")
        assert r.status_code == 200
        assert DESCRIPTION not in r.text
        assert "опубликованным профилем" in r.text


def test_feed_skips_other_specialties(client: TestClient, scene: dict) -> None:
    other, other_id = _browser(client, "other@example.com")
    _make_master(other_id, specs=("elektrik",))
    assert DESCRIPTION not in other.get("/feed").text


def test_feed_ignores_a_malformed_cursor(scene: dict) -> None:
    r = scene["ali"].get("/feed?after=not-a-cursor")
    assert r.status_code == 200 and DESCRIPTION in r.text


def test_account_links_masters_to_the_feed_and_responses(scene: dict) -> None:
    text = scene["ali"].get("/account").text
    assert 'href="/feed"' in text and 'href="/responses/mine"' in text
    assert 'href="/feed"' not in scene["plain"].get("/account").text


# --- the master's request page (Review Focus 1) -----------------------------------------------


def test_master_page_from_the_feed_shows_details_but_never_address_or_contacts(
    scene: dict,
) -> None:
    r = scene["ali"].get(f"/requests/{scene['rid']}")
    assert r.status_code == 200
    assert DESCRIPTION in r.text
    assert f'action="/requests/{scene["rid"]}/respond"' in r.text
    assert ADDRESS not in r.text and CLIENT_PHONE not in r.text and "ana@example.com" not in r.text


def test_request_not_in_feed_is_404_for_masters_and_users(client: TestClient, scene: dict) -> None:
    other, other_id = _browser(client, "other@example.com")
    _make_master(other_id, specs=("elektrik",))
    for browser in (other, scene["plain"]):
        r = browser.get(f"/requests/{scene['rid']}")
        assert r.status_code == 404
        assert DESCRIPTION not in r.text


def test_paused_request_is_404_for_a_master_who_did_not_respond(scene: dict) -> None:
    _update(JobRequest, scene["rid"], feed_paused_at=utcnow())
    r = scene["ali"].get(f"/requests/{scene['rid']}")
    assert r.status_code == 404 and DESCRIPTION not in r.text
    r = _respond(scene["ali"], scene["rid"])
    assert r.status_code == 409 and "достаточно мастеров" in r.text
    assert DESCRIPTION not in r.text


def test_respond_then_page_shows_the_response_without_contacts(scene: dict) -> None:
    rid = scene["rid"]
    r = _respond(
        scene["ali"], rid, message="Сделаю завтра", price_amount="1 500", price_unit="fixed"
    )
    assert r.status_code == 303 and r.headers["location"] == f"/requests/{rid}?done=responded"
    page = scene["ali"].get(r.headers["location"]).text
    assert "Отклик отправлен" in page and "Сделаю завтра" in page and "1500" in page
    assert ADDRESS not in page and CLIENT_PHONE not in page
    assert f'action="/requests/{rid}/withdraw"' in page
    assert f'action="/requests/{rid}/respond"' not in page
    mine = scene["ali"].get("/responses/mine").text
    assert f'href="/requests/{rid}"' in mine and "Ждёт выбора клиента" in mine
    assert ADDRESS not in mine


def test_double_respond_is_a_friendly_409(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    r = _respond(scene["ali"], rid)
    assert r.status_code == 409 and "уже откликнулись" in r.text
    assert _row(rid).responses_count == 1


@pytest.mark.parametrize(
    "data",
    [
        {"price_amount": "abc", "price_unit": "fixed"},
        {"price_amount": "100", "price_unit": ""},
        {"price_amount": "-5", "price_unit": "fixed"},
        {"price_amount": "1" * 40, "price_unit": "fixed"},
        {"message": "x" * 501},
    ],
)
def test_bad_response_form_is_422_and_keeps_the_text(scene: dict, data: dict[str, str]) -> None:
    r = _respond(scene["ali"], scene["rid"], **data)
    assert r.status_code == 422
    assert 'role="alert"' in r.text
    assert _row(scene["rid"]).responses_count == 0


def test_client_cannot_respond_to_own_request(scene: dict) -> None:
    _make_master(scene["ana_id"])
    r = _respond(scene["ana"], scene["rid"])
    assert r.status_code == 404
    assert _row(scene["rid"]).responses_count == 0


def test_someone_else_cannot_respond_to_a_request_outside_their_feed(
    client: TestClient, scene: dict
) -> None:
    other, other_id = _browser(client, "other@example.com")
    _make_master(other_id, specs=("elektrik",))
    for browser in (other, scene["plain"]):
        r = _respond(browser, scene["rid"])
        assert r.status_code in (404, 409)
        assert DESCRIPTION not in r.text
    assert _row(scene["rid"]).responses_count == 0


# --- withdraw ---------------------------------------------------------------------------------


def test_withdraw_then_request_is_back_in_the_feed(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    r = _post(scene["ali"], f"/requests/{rid}/withdraw")
    assert r.status_code == 303 and r.headers["location"] == "/responses/mine?done=withdrawn"
    assert "Отклик отозван" in scene["ali"].get(r.headers["location"]).text
    assert _row(rid).responses_count == 0
    again = _post(scene["ali"], f"/requests/{rid}/withdraw")  # stale double click
    assert again.status_code in (404, 409) and again.status_code != 500
    assert f'action="/requests/{rid}/respond"' in scene["ali"].get(f"/requests/{rid}").text


def test_someone_else_cannot_withdraw(client: TestClient, scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    boris, boris_id = _browser(client, "boris@example.com")
    _make_master(boris_id)
    for browser in (boris, scene["plain"], scene["ana"]):
        assert _post(browser, f"/requests/{rid}/withdraw").status_code == 404
    assert _row(rid).responses_count == 1


# --- the client's side: responses, choose, complete -------------------------------------------


def test_client_sees_responses_with_contacts_and_a_prefilled_choose_form(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid, message="Готов прийти сегодня").status_code == 303
    page = scene["ana"].get(f"/requests/{rid}").text
    assert "Готов прийти сегодня" in page and f"Мастер {scene['ali_id']}" in page
    assert f"@{MASTER_TG}" in page  # contacts for the signed-in client (D4)
    assert f'action="/requests/{rid}/choose"' in page
    assert f'value="{CLIENT_PHONE}"' in page
    assert ADDRESS in page  # the client's own page


def test_unpublished_responder_card_has_no_profile_link(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    with get_sessionmaker()() as db:
        slug = db.get(MasterProfile, scene["ali_profile"]).slug
    _update(MasterProfile, scene["ali_profile"], published_at=None)
    page = scene["ana"].get(f"/requests/{rid}").text
    assert f"Мастер {scene['ali_id']}" in page
    assert f'href="/m/{slug}"' not in page


def test_choose_needs_a_phone(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    r = _post(scene["ana"], f"/requests/{rid}/choose", {"response_id": str(resp), "phone": "abc"})
    assert r.status_code == 422 and "+992" in r.text and 'value="abc"' in r.text
    assert _row(rid).status == "open"


def test_choose_refuses_an_overlong_phone(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    form = {"response_id": str(resp), "phone": CLIENT_PHONE + " " * 40 + "1"}
    assert _post(scene["ana"], f"/requests/{rid}/choose", form).status_code == 422
    assert _row(rid).status == "open"


def test_choose_shows_the_phone_to_the_chosen_master_only(client: TestClient, scene: dict) -> None:
    rid = scene["rid"]
    boris, boris_id = _browser(client, "boris@example.com")
    _make_master(boris_id)
    assert _respond(scene["ali"], rid).status_code == 303
    assert _respond(boris, rid).status_code == 303
    # Before the choice nobody sees the phone.
    for browser in (scene["ali"], boris):
        assert CLIENT_PHONE not in browser.get(f"/requests/{rid}").text
    resp = _response_id(rid, scene["ali_profile"])
    r = _post(
        scene["ana"],
        f"/requests/{rid}/choose",
        {"response_id": str(resp), "phone": "+992 90 111 22 33"},
    )
    assert r.status_code == 303 and r.headers["location"] == f"/requests/{rid}?done=chosen"
    assert _row(rid).status == "master_chosen"
    chosen = scene["ali"].get(f"/requests/{rid}").text
    assert f'href="tel:{CLIENT_PHONE}"' in chosen and "Нигина" in chosen and "Вас выбрали" in chosen
    assert ADDRESS not in chosen and "ana@example.com" not in chosen
    other = boris.get(f"/requests/{rid}").text
    assert CLIENT_PHONE not in other and "выбрал другого мастера" in other
    # Stale double click: friendly, no second effect.
    again = _post(
        scene["ana"], f"/requests/{rid}/choose", {"response_id": str(resp), "phone": CLIENT_PHONE}
    )
    assert again.status_code == 409 and "состояние заявки изменилось" in again.text


def test_choose_a_withdrawn_response_is_a_friendly_409(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    assert _post(scene["ali"], f"/requests/{rid}/withdraw").status_code == 303
    r = _post(
        scene["ana"], f"/requests/{rid}/choose", {"response_id": str(resp), "phone": CLIENT_PHONE}
    )
    assert r.status_code == 409 and "отозвал" in r.text
    assert _row(rid).status == "open"


@pytest.mark.parametrize("response_id", ["", "abc", "999999", "1" * 30])
def test_choose_an_unknown_response_is_409_never_500(scene: dict, response_id: str) -> None:
    rid = scene["rid"]
    r = _post(
        scene["ana"], f"/requests/{rid}/choose", {"response_id": response_id, "phone": CLIENT_PHONE}
    )
    assert r.status_code == 409
    assert _row(rid).status == "open"


def test_someone_else_cannot_choose_or_complete(client: TestClient, scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    form = {"response_id": str(resp), "phone": CLIENT_PHONE}
    for browser in (scene["ali"], scene["plain"]):
        assert _post(browser, f"/requests/{rid}/choose", form).status_code == 404
    assert _row(rid).status == "open"
    assert _post(scene["ana"], f"/requests/{rid}/choose", form).status_code == 303
    for browser in (scene["ali"], scene["plain"]):
        assert _post(browser, f"/requests/{rid}/complete").status_code == 404
    assert _row(rid).status == "master_chosen"


def test_choose_with_a_response_of_another_request_changes_nothing(scene: dict) -> None:
    rid = scene["rid"]
    second = _make_request(scene["ana_id"])
    assert _respond(scene["ali"], second).status_code == 303
    foreign = _response_id(second, scene["ali_profile"])
    r = _post(
        scene["ana"],
        f"/requests/{rid}/choose",
        {"response_id": str(foreign), "phone": CLIENT_PHONE},
    )
    assert r.status_code == 409
    assert _row(rid).status == "open" and _row(second).status == "open"


def test_complete_then_double_click_is_friendly(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    assert (
        _post(
            scene["ana"],
            f"/requests/{rid}/choose",
            {"response_id": str(resp), "phone": CLIENT_PHONE},
        ).status_code
        == 303
    )
    page = scene["ana"].get(f"/requests/{rid}").text
    assert f'action="/requests/{rid}/complete"' in page
    assert f'action="/requests/{rid}/choose"' not in page
    r = _post(scene["ana"], f"/requests/{rid}/complete")
    assert r.status_code == 303 and r.headers["location"] == f"/requests/{rid}?done=completed"
    assert _row(rid).status == "completed"
    again = _post(scene["ana"], f"/requests/{rid}/complete")
    assert again.status_code == 409
    # The chosen master keeps the contact on a completed job.
    assert f'href="tel:{CLIENT_PHONE}"' in scene["ali"].get(f"/requests/{rid}").text


def test_chosen_master_cannot_withdraw(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    _post(
        scene["ana"], f"/requests/{rid}/choose", {"response_id": str(resp), "phone": CLIENT_PHONE}
    )
    r = _post(scene["ali"], f"/requests/{rid}/withdraw")
    assert r.status_code == 409 and "нельзя отозвать" in r.text
    assert _row(rid).status == "master_chosen"


# --- no longer available (spec D10.9) ---------------------------------------------------------


@pytest.mark.parametrize("how", ["hidden", "client_blocked"])
def test_responder_sees_no_longer_available_others_404(
    client: TestClient, scene: dict, how: str
) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    boris, boris_id = _browser(client, "boris@example.com")
    _make_master(boris_id)  # eligible, would match, never responded
    if how == "hidden":
        _update(JobRequest, rid, hidden_at=utcnow())
    else:
        _update(User, scene["ana_id"], blocked_at=utcnow())
    r = scene["ali"].get(f"/requests/{rid}")
    assert r.status_code == 200 and "больше недоступна" in r.text
    assert DESCRIPTION not in r.text and ADDRESS not in r.text and "<img" not in r.text
    mine = scene["ali"].get("/responses/mine").text
    assert "больше недоступна" in mine and DESCRIPTION not in mine
    for browser in (boris, scene["plain"]):
        r = browser.get(f"/requests/{rid}")
        assert r.status_code == 404 and DESCRIPTION not in r.text
    # Acting on it is refused without details.
    r = _post(scene["ali"], f"/requests/{rid}/withdraw")
    assert r.status_code in (303, 409) and DESCRIPTION not in r.text


def test_chosen_master_loses_contact_when_request_is_hidden(scene: dict) -> None:
    rid = scene["rid"]
    assert _respond(scene["ali"], rid).status_code == 303
    resp = _response_id(rid, scene["ali_profile"])
    _post(
        scene["ana"], f"/requests/{rid}/choose", {"response_id": str(resp), "phone": CLIENT_PHONE}
    )
    _update(JobRequest, rid, hidden_at=utcnow())
    r = scene["ali"].get(f"/requests/{rid}")
    assert r.status_code == 200 and CLIENT_PHONE not in r.text and DESCRIPTION not in r.text
