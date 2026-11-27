"""Choose a master, complete the job, and the chosen master's view of the client (spec D5, D10)."""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import JobRequest, JobResponse, MasterProfile, User
from app.schemas.requests import ResponseInput
from app.services import jobs
from app.services import requests as svc
from app.services import responses as response_svc
from app.services.requests import RequestError
from app.services.responses import ResponseError
from tests import feed_support as fs

PHONE = "+992901234567"


@pytest.fixture(autouse=True)
def only_own_requests(db: Session) -> None:
    fs.hide_other_requests(db)


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    """Every job the services enqueue, instead of the (no-op) queue."""
    calls: list[tuple] = []

    def record(db, kind, payload, *, dedupe_key=None, run_at=None):  # type: ignore[no-untyped-def]
        calls.append((kind, payload, dedupe_key, run_at))

    monkeypatch.setattr(jobs, "enqueue", record)
    return calls


def _code(fn, *args, **kw) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(RequestError) as exc:
        fn(*args, **kw)
    return exc.value.code


def _setup(db: Session, **post_kw) -> tuple[User, JobRequest, User, MasterProfile, JobResponse]:  # type: ignore[no-untyped-def]
    """A client's request with one response from a visible master."""
    town = fs.city_without_districts(db)
    client = fs.user(db, name="Фарида")
    request = fs.post(db, client, city_id=town.id, **post_kw)
    owner, profile = fs.master(db, city_ids=(town.id,))
    response = response_svc.respond(db, owner, request.id, ResponseInput(message="Готов"))
    return client, request, owner, profile, response


def _respond(db: Session, request: JobRequest) -> tuple[User, MasterProfile, JobResponse]:
    owner, profile = fs.master(db, city_ids=(request.city_id,))
    return owner, profile, response_svc.respond(db, owner, request.id, ResponseInput())


def _events(queued: list[tuple]) -> list[tuple]:
    return [q for q in queued if q[0] != "new_response"]


# --- choose -----------------------------------------------------------------------------------


def test_choose_a_master(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    client, request, _, profile, response = _setup(db)
    result = svc.choose_master(db, client, request.id, response.id, " +992 90 123-45-67 ")
    assert result.status == "master_chosen"
    assert result.chosen_master_id == profile.id
    assert result.master_chosen_at is not None
    assert result.client_phone == PHONE
    assert _events(queued) == [("chosen", {"request_id": request.id}, f"chosen:{request.id}", None)]


@pytest.mark.parametrize("phone", ["", "   ", "12", "+992 90 abc", "+1+2345678", "x" * 30])
def test_choose_needs_a_valid_phone(db: Session, queued, phone: str) -> None:  # type: ignore[no-untyped-def]
    client, request, _, _, response = _setup(db)
    assert _code(svc.choose_master, db, client, request.id, response.id, phone) == "phone"
    assert request.status == "open" and request.chosen_master_id is None
    assert _events(queued) == []


def test_choose_twice_is_state(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    """A stale double-click (or a second tab choosing someone else) changes nothing."""
    client, request, _, profile, response = _setup(db)
    _, _, other = _respond(db, request)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    assert _code(svc.choose_master, db, client, request.id, response.id, PHONE) == "state"
    assert _code(svc.choose_master, db, client, request.id, other.id, PHONE) == "state"
    assert request.chosen_master_id == profile.id
    assert len(_events(queued)) == 1


def test_only_a_responder_of_this_request_can_be_chosen(db: Session) -> None:
    client, request, owner, _, _ = _setup(db)
    elsewhere = fs.post(db, client, city_id=request.city_id)
    foreign = response_svc.respond(db, owner, elsewhere.id, ResponseInput())
    assert _code(svc.choose_master, db, client, request.id, foreign.id, PHONE) == "not_found"
    assert _code(svc.choose_master, db, client, request.id, 10**12, PHONE) == "not_found"
    assert request.status == "open"


def test_someone_else_cannot_choose(db: Session) -> None:
    _, request, owner, _, response = _setup(db)
    for intruder in (fs.user(db), owner):
        assert _code(svc.choose_master, db, intruder, request.id, response.id, PHONE) == (
            "not_found"
        )
    assert request.status == "open"


def test_blocked_client_cannot_choose(db: Session) -> None:
    client, request, _, _, response = _setup(db)
    client.blocked_at = utcnow()
    db.flush()
    assert _code(svc.choose_master, db, client, request.id, response.id, PHONE) == "inactive"


def test_withdrawn_response_cannot_be_chosen(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    client, request, owner, _, response = _setup(db)
    response_svc.withdraw(db, owner, request.id)
    assert _code(svc.choose_master, db, client, request.id, response.id, PHONE) == "withdrawn"
    assert request.status == "open" and request.chosen_master_id is None
    assert _events(queued) == []


@pytest.mark.parametrize("why", ["blocked", "deleted", "hidden"])
def test_blocked_master_is_not_choosable(db: Session, why: str) -> None:
    client, request, owner, profile, response = _setup(db)
    if why == "hidden":
        profile.hidden_at = utcnow()
    else:
        setattr(owner, f"{why}_at", utcnow())
    db.flush()
    assert _code(svc.choose_master, db, client, request.id, response.id, PHONE) == "not_found"
    assert request.status == "open"


def test_unpublished_master_stays_choosable(db: Session) -> None:
    """Existing responses stay when a master unpublishes (spec D10.5)."""
    client, request, _, profile, response = _setup(db)
    profile.published_at = None
    db.flush()
    svc.choose_master(db, client, request.id, response.id, PHONE)
    assert request.chosen_master_id == profile.id


@pytest.mark.parametrize("status", ["closed", "cancelled"])
def test_choose_on_a_closed_request_is_state(db: Session, status: str) -> None:
    client, request, _, _, response = _setup(db)
    svc.close_request(db, client, request.id, status)
    assert _code(svc.choose_master, db, client, request.id, response.id, PHONE) == "state"


def test_chosen_master_cannot_withdraw_after_choose(db: Session) -> None:
    client, request, owner, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    with pytest.raises(ResponseError) as exc:
        response_svc.withdraw(db, owner, request.id)
    assert exc.value.code == "chosen"


def test_contact_phone_prefill(db: Session) -> None:
    client = fs.user(db)
    assert svc.contact_phone_prefill(client) is None
    client.phone = PHONE
    assert svc.contact_phone_prefill(client) is None  # unverified
    client.phone_verified_at = utcnow()
    assert svc.contact_phone_prefill(client) == PHONE


# --- complete ---------------------------------------------------------------------------------


def test_complete_the_job(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    client, request, _, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    result = svc.complete_request(db, client, request.id)
    assert result.status == "completed" and result.completed_at is not None
    assert _events(queued)[-1] == (
        "review_prompt",
        {"request_id": request.id},
        f"review_prompt:{request.id}",
        None,
    )
    # a stale second click
    assert _code(svc.complete_request, db, client, request.id) == "state"
    assert [q[0] for q in _events(queued)] == ["chosen", "review_prompt"]


def test_complete_needs_a_chosen_master(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    client, request, _, _, _ = _setup(db)
    assert _code(svc.complete_request, db, client, request.id) == "state"
    svc.close_request(db, client, request.id, "closed")
    assert _code(svc.complete_request, db, client, request.id) == "state"
    assert _events(queued) == []


def test_someone_else_cannot_complete(db: Session) -> None:
    client, request, owner, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    for intruder in (fs.user(db), owner):
        assert _code(svc.complete_request, db, intruder, request.id) == "not_found"
    assert request.status == "master_chosen"


def test_blocked_client_cannot_complete(db: Session) -> None:
    client, request, _, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    client.blocked_at = utcnow()
    db.flush()
    assert _code(svc.complete_request, db, client, request.id) == "inactive"


# --- close / cancel after choosing (spec D10.3) -----------------------------------------------


@pytest.mark.parametrize("reason", ["closed", "cancelled"])
def test_close_after_choosing_tells_the_chosen_master(db: Session, queued, reason: str) -> None:  # type: ignore[no-untyped-def]
    client, request, _, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    svc.close_request(db, client, request.id, reason)
    assert _events(queued)[-1] == (
        "request_cancelled",
        {"request_id": request.id},
        f"request_cancelled:{request.id}",
        None,
    )


@pytest.mark.parametrize("reason", ["closed", "cancelled"])
def test_close_before_choosing_tells_nobody(db: Session, queued, reason: str) -> None:  # type: ignore[no-untyped-def]
    client, request, _, _, _ = _setup(db)
    svc.close_request(db, client, request.id, reason)
    assert _events(queued) == []


# --- the chosen master's view of the client (spec D10.1) --------------------------------------


def test_contact_only_for_the_chosen_master(db: Session) -> None:
    client, request, owner, _, response = _setup(db, address="ул. Рудаки, 1, кв. 5")
    other_owner, _, _ = _respond(db, request)
    assert svc.client_contact_for_master(db, owner, request.id) is None  # before choosing
    svc.choose_master(db, client, request.id, response.id, PHONE)
    contact = svc.client_contact_for_master(db, owner, request.id)
    assert contact is not None
    assert (contact.name, contact.phone) == ("Фарида", PHONE)
    dumped = contact.model_dump_json()
    assert client.email not in dumped and "Рудаки" not in dumped
    assert svc.client_contact_for_master(db, other_owner, request.id) is None
    assert svc.client_contact_for_master(db, client, request.id) is None
    assert svc.client_contact_for_master(db, fs.user(db), request.id) is None
    assert svc.client_contact_for_master(db, owner, 10**12) is None


def test_contact_kept_after_completion(db: Session) -> None:
    client, request, owner, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    svc.complete_request(db, client, request.id)
    assert svc.client_contact_for_master(db, owner, request.id) is not None


@pytest.mark.parametrize("reason", ["closed", "cancelled"])
def test_contact_gone_after_close(db: Session, reason: str) -> None:
    client, request, owner, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    svc.close_request(db, client, request.id, reason)
    assert svc.client_contact_for_master(db, owner, request.id) is None


@pytest.mark.parametrize("who", ["client_blocked", "client_deleted", "master_blocked", "hidden"])
def test_contact_gone_when_someone_is_blocked(db: Session, who: str) -> None:
    client, request, owner, _, response = _setup(db)
    svc.choose_master(db, client, request.id, response.id, PHONE)
    if who == "client_blocked":
        client.blocked_at = utcnow()
    elif who == "client_deleted":
        client.deleted_at = utcnow()
    elif who == "master_blocked":
        owner.blocked_at = utcnow()
    else:
        request.hidden_at = utcnow()
    db.flush()
    assert svc.client_contact_for_master(db, owner, request.id) is None


def test_contact_name_may_be_missing(db: Session) -> None:
    client, request, owner, _, response = _setup(db)
    client.name = None
    svc.choose_master(db, client, request.id, response.id, PHONE)
    contact = svc.client_contact_for_master(db, owner, request.id)
    assert contact is not None and contact.name is None


# --- the client's list of responses (spec D4, D10.2, D10.5) -----------------------------------


def test_responses_for_client(db: Session) -> None:
    client, request, owner, profile, response = _setup(db)
    profile.contact_phone, profile.contact_telegram = "+992907654321", "usto_m"
    db.flush()
    (card,) = svc.responses_for_client(db, client, request.id)
    assert card.response_id == response.id
    assert card.slug == profile.slug and card.display_name == profile.display_name
    assert card.message == "Готов"
    assert card.responded_at == response.updated_at
    assert card.contacts is not None
    assert card.contacts.phone == "+992907654321" and card.contacts.telegram == "usto_m"
    assert card.chosen is False and card.choosable is True and card.published is True
    assert owner.email not in card.model_dump_json()
    client.blocked_at = utcnow()
    (card,) = svc.responses_for_client(db, client, request.id)
    assert card.contacts is None and card.choosable is False


def test_responses_for_client_hide_withdrawn_blocked_and_hidden(db: Session) -> None:
    client, request, _, _, kept = _setup(db)
    withdrawn_owner, _, _ = _respond(db, request)
    response_svc.withdraw(db, withdrawn_owner, request.id)
    blocked_owner, _, _ = _respond(db, request)
    deleted_owner, _, _ = _respond(db, request)
    _, hidden, _ = _respond(db, request)
    blocked_owner.blocked_at = utcnow()
    deleted_owner.deleted_at = utcnow()
    hidden.hidden_at = utcnow()
    db.flush()
    assert [c.response_id for c in svc.responses_for_client(db, client, request.id)] == [kept.id]


def test_responses_for_client_newest_first_and_chosen(db: Session) -> None:
    client, request, _, _, first = _setup(db)
    _, _, second = _respond(db, request)
    first.updated_at = utcnow().replace(year=2020)
    db.flush()
    cards = svc.responses_for_client(db, client, request.id)
    assert [c.response_id for c in cards] == [second.id, first.id]
    svc.choose_master(db, client, request.id, first.id, PHONE)
    cards = {c.response_id: c for c in svc.responses_for_client(db, client, request.id)}
    assert cards[first.id].chosen and not cards[second.id].chosen
    assert not any(c.choosable for c in cards.values())


def test_responses_for_client_unpublished_master_has_no_profile_link(db: Session) -> None:
    client, request, _, profile, _ = _setup(db)
    profile.published_at = None
    db.flush()
    (card,) = svc.responses_for_client(db, client, request.id)
    assert card.published is False and card.choosable is True


def test_responses_for_client_only_for_the_owner(db: Session) -> None:
    _, request, owner, _, _ = _setup(db)
    with pytest.raises(RequestError) as exc:
        svc.responses_for_client(db, owner, request.id)
    assert exc.value.code == "not_found"


def test_my_responses_show_the_latest_response_date(db: Session) -> None:
    """A re-respond after a withdrawal shows its new date (the row's updated_at)."""
    client, request, owner, _, response = _setup(db)
    response.created_at = utcnow().replace(year=2020)
    db.flush()
    (card,) = response_svc.my_responses(db, owner)
    assert card.responded_at == response.updated_at != response.created_at
    assert request.client_id == client.id


# --- choose vs withdraw from two sessions (Review Focus 2) ------------------------------------


def test_choose_and_withdraw_from_two_sessions_agree(migrated_engine) -> None:
    factory = get_sessionmaker()
    with factory() as s:
        client, request, owner, profile, response = _setup(s)
        s.commit()
        client_id, owner_id, master_id = client.id, owner.id, profile.id
        request_id, response_id = request.id, response.id
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def choose() -> None:
        with factory() as s:
            user = s.get(User, client_id)
            barrier.wait()
            try:
                svc.choose_master(s, user, request_id, response_id, PHONE)
                s.commit()
                results["choose"] = "ok"
            except RequestError as exc:
                s.rollback()
                results["choose"] = exc.code

    def withdraw() -> None:
        with factory() as s:
            user = s.get(User, owner_id)
            barrier.wait()
            try:
                response_svc.withdraw(s, user, request_id)
                s.commit()
                results["withdraw"] = "ok"
            except ResponseError as exc:
                s.rollback()
                results["withdraw"] = exc.code

    try:
        threads = [threading.Thread(target=choose), threading.Thread(target=withdraw)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert results in (
            {"choose": "ok", "withdraw": "chosen"},
            {"choose": "withdrawn", "withdraw": "ok"},
        )
        with factory() as s:
            row = s.get(JobRequest, request_id)
            resp = s.get(JobResponse, response_id)
            if results["choose"] == "ok":
                assert row.status == "master_chosen" and row.chosen_master_id == master_id
                assert resp.withdrawn_at is None and row.responses_count == 1
            else:
                assert row.status == "open" and row.chosen_master_id is None
                assert resp.withdrawn_at is not None and row.responses_count == 0
    finally:
        with factory() as s:
            s.execute(delete(JobRequest).where(JobRequest.id == request_id))
            s.execute(delete(MasterProfile).where(MasterProfile.id == master_id))
            s.execute(delete(User).where(User.id.in_([owner_id, client_id])))
            s.commit()


def test_double_choose_from_two_sessions_chooses_once(migrated_engine) -> None:
    factory = get_sessionmaker()
    with factory() as s:
        client, request, owner, profile, response = _setup(s)
        other_owner, other_profile, other = _respond(s, request)
        s.commit()
        client_id, request_id = client.id, request.id
        owners = [owner.id, other_owner.id]
        masters = [profile.id, other_profile.id]
        responses = [response.id, other.id]
    barrier = threading.Barrier(2)
    results: list[str] = []

    def choose(response_id: int) -> None:
        with factory() as s:
            user = s.get(User, client_id)
            barrier.wait()
            try:
                svc.choose_master(s, user, request_id, response_id, PHONE)
                s.commit()
                results.append("ok")
            except RequestError as exc:
                s.rollback()
                results.append(exc.code)

    try:
        threads = [threading.Thread(target=choose, args=(r,)) for r in responses]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(results) == ["ok", "state"]
        with factory() as s:
            row = s.get(JobRequest, request_id)
            assert row.status == "master_chosen" and row.chosen_master_id in masters
    finally:
        with factory() as s:
            s.execute(delete(JobRequest).where(JobRequest.id == request_id))
            s.execute(delete(MasterProfile).where(MasterProfile.id.in_(masters)))
            s.execute(delete(User).where(User.id.in_([*owners, client_id])))
            s.commit()


def test_decimal_price_shown_to_client(db: Session) -> None:
    client, request, _, _, _ = _setup(db)
    owner, _ = fs.master(db, city_ids=(request.city_id,))
    response_svc.respond(
        db, owner, request.id, ResponseInput(price_amount=Decimal("150.5"), price_unit="m2")
    )
    cards = svc.responses_for_client(db, client, request.id)
    prices = {(c.price_amount, c.price_unit) for c in cards}
    assert (Decimal("150.50"), "m2") in prices
