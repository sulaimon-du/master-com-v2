import threading
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import JobRequest, JobResponse, MasterProfile, User
from app.models.request import FEED_PAUSE_STEP
from app.schemas.requests import ResponseInput
from app.services import feed as feed_svc
from app.services import jobs
from app.services import requests as request_svc
from app.services import responses as svc
from app.services.responses import ResponseError
from tests import feed_support as fs


@pytest.fixture(autouse=True)
def only_own_requests(db: Session) -> None:
    fs.hide_other_requests(db)


@pytest.fixture(autouse=True)
def queued(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    """Every job the responses service enqueues, instead of the real queue (tested in test_jobs).

    "new_request" comes from posting the test's request (covered in test_requests_service).
    """
    calls: list[tuple] = []

    def record(db, kind, payload, *, dedupe_key=None, run_at=None):  # type: ignore[no-untyped-def]
        if kind != "new_request":
            calls.append((kind, payload, dedupe_key, run_at))

    monkeypatch.setattr(jobs, "enqueue", record)
    return calls


def _code(fn, *args, **kw) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ResponseError) as exc:
        fn(*args, **kw)
    return exc.value.code


def _setup(db: Session, **post_kw) -> tuple[User, MasterProfile, JobRequest]:  # type: ignore[no-untyped-def]
    town = fs.city_without_districts(db)
    owner, profile = fs.master(db, city_ids=(town.id,))
    return owner, profile, fs.post(db, city_id=town.id, **post_kw)


def _another_master(db: Session, request: JobRequest) -> tuple[User, MasterProfile]:
    return fs.master(db, city_ids=(request.city_id,))


def _active(db: Session, request: JobRequest) -> int:
    return len(
        db.scalars(
            select(JobResponse).where(
                JobResponse.request_id == request.id, JobResponse.withdrawn_at.is_(None)
            )
        ).all()
    )


# --- respond ----------------------------------------------------------------------------------


def test_respond_creates_one_response_and_counts_it(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    owner, profile, request = _setup(db)
    data = ResponseInput(
        message="  Сделаю завтра  ", price_amount=Decimal("150"), price_unit="fixed"
    )
    response = svc.respond(db, owner, request.id, data)
    assert response.master_id == profile.id
    assert response.message == "Сделаю завтра"
    assert (response.price_amount, response.price_unit) == (Decimal("150"), "fixed")
    assert request.responses_count == 1
    assert request.feed_paused_at is None
    assert queued == [
        ("new_response", {"response_id": response.id}, f"new_response:{response.id}", None)
    ]


def test_respond_without_message_or_price(db: Session) -> None:
    owner, _, request = _setup(db)
    response = svc.respond(db, owner, request.id, ResponseInput(message="   "))
    assert response.message is None
    assert response.price_amount is None and response.price_unit is None


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (ResponseInput(message="x" * 501), "message"),
        (ResponseInput(price_amount=Decimal("10")), "price"),
        (ResponseInput(price_unit="fixed"), "price"),
        (ResponseInput(price_amount=Decimal("0"), price_unit="fixed"), "price"),
        (ResponseInput(price_amount=Decimal("-5"), price_unit="fixed"), "price"),
        (ResponseInput(price_amount=Decimal("1.234"), price_unit="fixed"), "price"),
        (ResponseInput.model_construct(price_amount=Decimal("NaN"), price_unit="fixed"), "price"),
        (ResponseInput(price_amount=Decimal("1e12"), price_unit="fixed"), "price"),
        (ResponseInput(price_amount=Decimal("10"), price_unit="kg"), "price"),
    ],
)
def test_respond_validates_input(db: Session, queued, data: ResponseInput, code: str) -> None:  # type: ignore[no-untyped-def]
    owner, _, request = _setup(db)
    assert _code(svc.respond, db, owner, request.id, data) == code
    assert request.responses_count == 0
    assert queued == []


def test_message_of_500_characters_is_accepted(db: Session) -> None:
    owner, _, request = _setup(db)
    assert svc.respond(db, owner, request.id, ResponseInput(message="я" * 500)).message


def test_respond_twice_is_already(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    owner, _, request = _setup(db)
    svc.respond(db, owner, request.id, ResponseInput())
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "already"
    assert request.responses_count == 1
    assert len(queued) == 1


def test_respond_needs_a_visible_master(db: Session) -> None:
    town = fs.city_without_districts(db)
    request = fs.post(db, city_id=town.id)
    draft, _ = fs.master(db, ready=False)
    assert _code(svc.respond, db, draft, request.id, ResponseInput()) == "not_master"
    assert _code(svc.respond, db, fs.user(db), request.id, ResponseInput()) == "not_master"


def test_blocked_master_cannot_respond(db: Session) -> None:
    owner, _, request = _setup(db)
    owner.blocked_at = utcnow()
    db.flush()
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "inactive"


def test_respond_to_a_foreign_or_unknown_id_is_not_found(db: Session) -> None:
    owner, _, request = _setup(db)
    other_city = fs.city(db, "dushanbe")
    elsewhere = fs.post(
        db, city_id=other_city.id, district_id=fs.district(db, "dushanbe", "sino").id
    )
    wrong_spec = fs.post(db, specs=("arkhitektor",), city_id=request.city_id)
    own = fs.post(db, owner, city_id=request.city_id)
    hidden = fs.post(db, city_id=request.city_id)
    hidden.hidden_at = utcnow()
    db.flush()
    for request_id in (elsewhere.id, wrong_spec.id, own.id, hidden.id, 10**9):
        assert _code(svc.respond, db, owner, request_id, ResponseInput()) == "not_found"
    assert own.responses_count == 0


def test_respond_to_a_blocked_clients_request_is_not_found(db: Session) -> None:
    owner, _, request = _setup(db)
    client = db.get(User, request.client_id)
    assert client is not None
    client.blocked_at = utcnow()
    db.flush()
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "not_found"


def test_respond_to_a_closed_or_paused_request(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    owner, _, request = _setup(db)
    request.feed_paused_at = utcnow()
    db.flush()
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "paused"
    request_svc.close_request(db, db.get(User, request.client_id), request.id, "closed")
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "state"
    assert queued == []


def test_fifteenth_response_pauses_the_feed(db: Session) -> None:
    _, _, request = _setup(db)
    for i in range(FEED_PAUSE_STEP):
        assert request.feed_paused_at is None, i
        owner, _ = _another_master(db, request)
        svc.respond(db, owner, request.id, ResponseInput())
    assert request.responses_count == FEED_PAUSE_STEP
    assert request.feed_paused_at is not None
    late, _ = _another_master(db, request)
    assert _code(svc.respond, db, late, request.id, ResponseInput()) == "paused"
    assert feed_svc.feed(db, late, None).items == []


def test_reopened_request_pauses_again_after_fifteen_more(db: Session) -> None:
    _, _, request = _setup(db)
    request.responses_count = FEED_PAUSE_STEP - 1
    db.flush()
    owner, _ = _another_master(db, request)
    svc.respond(db, owner, request.id, ResponseInput())
    assert request.feed_paused_at is not None
    request_svc.reopen_feed(db, db.get(User, request.client_id), request.id)
    assert request.feed_pause_at == 2 * FEED_PAUSE_STEP
    owner, _ = _another_master(db, request)
    svc.respond(db, owner, request.id, ResponseInput())
    assert request.feed_paused_at is None


# --- withdraw and respond again (spec D10.2) --------------------------------------------------


def test_withdraw_and_respond_again_reuses_the_row(db: Session, queued) -> None:  # type: ignore[no-untyped-def]
    owner, _, request = _setup(db)
    first = svc.respond(db, owner, request.id, ResponseInput(message="Первый"))
    svc.withdraw(db, owner, request.id)
    assert first.withdrawn_at is not None
    assert request.responses_count == 0
    assert _code(svc.withdraw, db, owner, request.id) == "withdrawn"
    again = svc.respond(
        db, owner, request.id, ResponseInput(price_amount=Decimal("20"), price_unit="hour")
    )
    assert again.id == first.id
    assert again.withdrawn_at is None
    assert again.message is None
    assert (again.price_amount, again.price_unit) == (Decimal("20"), "hour")
    assert request.responses_count == 1 == _active(db, request)
    # The client hears about a master once, however often they withdraw and come back.
    assert [q[2] for q in queued] == [f"new_response:{first.id}"] * 2


def test_withdraw_never_unpauses(db: Session) -> None:
    owner, _, request = _setup(db)
    request.responses_count = FEED_PAUSE_STEP - 1
    db.flush()
    svc.respond(db, owner, request.id, ResponseInput())
    assert request.feed_paused_at is not None
    svc.withdraw(db, owner, request.id)
    assert request.responses_count == FEED_PAUSE_STEP - 1
    assert request.feed_paused_at is not None
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "paused"


def test_withdraw_of_someone_elses_response_is_not_found(db: Session) -> None:
    owner, _, request = _setup(db)
    svc.respond(db, owner, request.id, ResponseInput())
    other, _ = _another_master(db, request)
    assert _code(svc.withdraw, db, other, request.id) == "not_found"
    assert _code(svc.withdraw, db, fs.user(db), request.id) == "not_found"
    assert _code(svc.withdraw, db, owner, 10**9) == "not_found"
    assert request.responses_count == 1


def test_chosen_master_cannot_withdraw(db: Session) -> None:
    owner, profile, request = _setup(db)
    svc.respond(db, owner, request.id, ResponseInput())
    request.status, request.chosen_master_id = "master_chosen", profile.id
    request.master_chosen_at, request.client_phone = utcnow(), "+992901234567"
    db.flush()
    assert _code(svc.withdraw, db, owner, request.id) == "chosen"
    assert request.responses_count == 1


def test_other_master_can_withdraw_after_someone_was_chosen(db: Session) -> None:
    owner, _, request = _setup(db)
    svc.respond(db, owner, request.id, ResponseInput())
    chosen_owner, chosen = _another_master(db, request)
    svc.respond(db, chosen_owner, request.id, ResponseInput())
    request.status, request.chosen_master_id = "master_chosen", chosen.id
    request.master_chosen_at, request.client_phone = utcnow(), "+992901234567"
    db.flush()
    svc.withdraw(db, owner, request.id)
    assert request.responses_count == 1
    assert _code(svc.respond, db, owner, request.id, ResponseInput()) == "state"


def test_unpublished_master_can_still_withdraw(db: Session) -> None:
    owner, profile, request = _setup(db)
    svc.respond(db, owner, request.id, ResponseInput())
    profile.hidden_at = utcnow()
    db.flush()
    svc.withdraw(db, owner, request.id)
    assert request.responses_count == 0


# --- my responses (spec D10.9) ----------------------------------------------------------------


def _choose(request: JobRequest, master_id: int) -> None:
    request.status, request.chosen_master_id = "master_chosen", master_id
    request.master_chosen_at, request.client_phone = utcnow(), "+992901234567"


def test_my_responses_statuses(db: Session) -> None:
    owner, profile, _ = _setup(db)
    city_id = fs.city_without_districts(db).id
    other_owner, other = fs.master(db, city_ids=(city_id,))
    made = {name: fs.post(db, city_id=city_id) for name in ("open", "paused", "mine", "theirs")}
    made |= {name: fs.post(db, city_id=city_id) for name in ("closed", "cancelled", "done")}
    made |= {name: fs.post(db, city_id=city_id) for name in ("hidden", "blocked", "deleted")}
    for request in made.values():
        svc.respond(db, owner, request.id, ResponseInput(message="Готов"))
    svc.respond(db, other_owner, made["theirs"].id, ResponseInput())
    made["paused"].feed_paused_at = utcnow()
    _choose(made["mine"], profile.id)
    _choose(made["theirs"], other.id)
    _choose(made["done"], profile.id)
    made["done"].status, made["done"].completed_at = "completed", utcnow()
    for name in ("closed", "cancelled"):
        made[name].status, made[name].closed_at = name, utcnow()
        made[name].close_reason = f"client_{name}"
    made["hidden"].hidden_at = utcnow()
    db.get(User, made["blocked"].client_id).blocked_at = utcnow()
    db.get(User, made["deleted"].client_id).deleted_at = utcnow()
    db.flush()

    cards = {c.request_id: c for c in svc.my_responses(db, owner)}
    status = {name: cards[r.id].status for name, r in made.items()}
    assert status == {
        "open": "open",
        "paused": "open",
        "mine": "chosen",
        "theirs": "other_chosen",
        "closed": "closed",
        "cancelled": "cancelled",
        "done": "chosen",
        "hidden": "unavailable",
        "blocked": "unavailable",
        "deleted": "unavailable",
    }
    open_card = cards[made["open"].id]
    assert open_card.description and open_card.place and open_card.specialties
    assert open_card.message == "Готов"
    for name in ("hidden", "blocked", "deleted"):
        card = cards[made[name].id]
        assert card.description is None and card.place is None and card.specialties == []
        assert card.thumb_url is None


def test_my_responses_skip_withdrawn_and_other_masters(db: Session) -> None:
    owner, _, request = _setup(db)
    kept = fs.post(db, city_id=request.city_id)
    svc.respond(db, owner, request.id, ResponseInput())
    svc.respond(db, owner, kept.id, ResponseInput())
    svc.withdraw(db, owner, request.id)
    other, _ = _another_master(db, request)
    svc.respond(db, other, request.id, ResponseInput())
    assert [c.request_id for c in svc.my_responses(db, owner)] == [kept.id]
    assert svc.my_responses(db, fs.user(db)) == []


def test_my_responses_never_carry_the_address(db: Session) -> None:
    owner, _, request = _setup(db, address="ул. Рудаки, 1, кв. 5")
    svc.respond(db, owner, request.id, ResponseInput())
    (card,) = svc.my_responses(db, owner)
    assert "Рудаки" not in card.model_dump_json()


# --- double submit from two tabs (Review Focus 2) ---------------------------------------------


def test_double_respond_from_two_sessions_makes_one_row(migrated_engine) -> None:
    factory = get_sessionmaker()
    with factory() as s:
        owner, profile, request = _setup(s)
        client_id = request.client_id
        s.commit()
        ids = (owner.id, profile.id, request.id)
    owner_id, master_id, request_id = ids
    barrier = threading.Barrier(2)
    results: list[str] = []

    def respond() -> None:
        with factory() as s:
            user = s.get(User, owner_id)
            barrier.wait()
            try:
                svc.respond(s, user, request_id, ResponseInput())
                s.commit()
                results.append("ok")
            except ResponseError as exc:
                s.rollback()
                results.append(exc.code)

    try:
        threads = [threading.Thread(target=respond) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(results) == ["already", "ok"]
        with factory() as s:
            rows = s.scalars(select(JobResponse).where(JobResponse.request_id == request_id))
            assert len(rows.all()) == 1
            assert s.get(JobRequest, request_id).responses_count == 1
    finally:
        with factory() as s:
            s.execute(delete(JobRequest).where(JobRequest.id == request_id))
            s.execute(delete(MasterProfile).where(MasterProfile.id == master_id))
            s.execute(delete(User).where(User.id.in_([owner_id, client_id])))
            s.commit()
