"""C4/C5 timers and the purge of expired sign-in rows, with a frozen clock (Review Focus 5).

Ages count from the stored UTC timestamps. T is 19:30 UTC, already the next calendar day in
Dushanbe (UTC+5), so any rounding to local days would show up at the edges.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.core.security import hash_token
from app.core.time import utcnow
from app.models import JobRequest, User
from app.models.auth import AuthSession, AuthToken, OAuthFlow, TelegramSignin
from app.models.jobs import Job
from app.schemas.requests import ResponseInput
from app.services import requests as request_svc
from app.services import responses as response_svc
from app.services.requests import RequestError
from app.worker import periodic
from tests import feed_support as fs

T = datetime(2020, 3, 1, 19, 30, tzinfo=UTC)
SECOND = timedelta(seconds=1)
DAY = timedelta(days=1)
PHONE = "+992901234567"


def _open(db: Session, opened_at: datetime = T) -> JobRequest:
    request = fs.post(db, city_id=fs.city_without_districts(db).id)
    request.opened_at = opened_at
    db.flush()
    return request


def _chosen(db: Session, chosen_at: datetime = T) -> JobRequest:
    town = fs.city_without_districts(db)
    client = fs.user(db)
    request = fs.post(db, client, city_id=town.id)
    owner, _ = fs.master(db, city_ids=(town.id,))
    response = response_svc.respond(db, owner, request.id, ResponseInput())
    request_svc.choose_master(db, client, request.id, response.id, PHONE)
    request.master_chosen_at = chosen_at
    db.flush()
    return request


def _jobs(db: Session, kind: str, request_id: int) -> list[Job]:
    rows = db.scalars(select(Job).where(Job.kind == kind)).all()
    return [j for j in rows if j.payload.get("request_id") == request_id]


def _reload(db: Session, request: JobRequest) -> JobRequest:
    db.refresh(request)
    return request


# --- C4: open requests ------------------------------------------------------------------------


def test_c4_reminds_exactly_at_27_days(db: Session) -> None:
    request = _open(db)
    _, reminded = periodic.expire_requests(db, T + 27 * DAY - SECOND)
    assert request.id not in reminded and _reload(db, request).expiry_reminded_at is None
    assert _jobs(db, "expiry_reminder", request.id) == []

    now = T + 27 * DAY
    closed, reminded = periodic.expire_requests(db, now)
    assert request.id in reminded and request.id not in closed
    row = _reload(db, request)
    assert (row.status, row.expiry_reminded_at) == ("open", now)
    (job,) = _jobs(db, "expiry_reminder", request.id)
    assert job.dedupe_key == f"expiry_reminder:{request.id}:{int(T.timestamp())}"

    # The next windows do not remind again.
    _, reminded = periodic.expire_requests(db, now + DAY)
    assert request.id not in reminded and len(_jobs(db, "expiry_reminder", request.id)) == 1


def test_c4_closes_exactly_at_30_days(db: Session) -> None:
    request = _open(db)
    closed, _ = periodic.expire_requests(db, T + 30 * DAY - SECOND)
    assert request.id not in closed and _reload(db, request).status == "open"

    now = T + 30 * DAY
    closed, reminded = periodic.expire_requests(db, now)
    assert request.id in closed and request.id not in reminded
    row = _reload(db, request)
    assert (row.status, row.close_reason, row.closed_at) == ("closed", "auto_expired", now)


def test_c4_past_30_days_closes_without_a_late_reminder(db: Session) -> None:
    request = _open(db)  # the worker was down from day 26 to day 31
    closed, reminded = periodic.expire_requests(db, T + 31 * DAY)
    assert request.id in closed and request.id not in reminded
    assert _jobs(db, "expiry_reminder", request.id) == []


def test_c4_counts_from_the_last_reopen(db: Session) -> None:
    request = _open(db, opened_at=utcnow() - 28 * DAY)
    periodic.expire_requests(db, utcnow())
    assert len(_jobs(db, "expiry_reminder", request.id)) == 1
    request.feed_paused_at = utcnow()
    db.flush()
    client = db.get(User, request.client_id)
    request_svc.reopen_feed(db, client, request.id)
    reopened = _reload(db, request).opened_at

    closed, _ = periodic.expire_requests(db, reopened + 27 * DAY - SECOND)
    assert request.id not in closed and _reload(db, request).status == "open"
    _, reminded = periodic.expire_requests(db, reopened + 27 * DAY)
    assert request.id in reminded and len(_jobs(db, "expiry_reminder", request.id)) == 2


def test_c4_leaves_other_statuses_alone(db: Session) -> None:
    chosen = _chosen(db)
    chosen.opened_at = T
    closed_req = _open(db)
    request_svc.close_request(db, db.get(User, closed_req.client_id), closed_req.id, "closed")
    closed, reminded = periodic.expire_requests(db, T + 60 * DAY)
    assert {chosen.id, closed_req.id}.isdisjoint(closed + reminded)
    assert _reload(db, closed_req).close_reason == "client_closed"


# --- C5: a master was chosen ------------------------------------------------------------------


def test_c5_asks_exactly_at_14_days(db: Session) -> None:
    request = _chosen(db)
    _, asked = periodic.stale_requests(db, T + 14 * DAY - SECOND)
    assert request.id not in asked and _reload(db, request).done_asked_at is None

    now = T + 14 * DAY
    closed, asked = periodic.stale_requests(db, now)
    assert request.id in asked and request.id not in closed
    assert _reload(db, request).done_asked_at == now
    (job,) = _jobs(db, "done_check", request.id)
    assert job.dedupe_key == f"done_check:{request.id}"
    _, asked = periodic.stale_requests(db, now + DAY)
    assert request.id not in asked


def test_c5_closes_exactly_at_30_days_without_a_review_prompt(db: Session) -> None:
    request = _chosen(db)
    closed, _ = periodic.stale_requests(db, T + 30 * DAY - SECOND)
    assert request.id not in closed and _reload(db, request).status == "master_chosen"

    now = T + 30 * DAY
    closed, _ = periodic.stale_requests(db, now)
    assert request.id in closed
    row = _reload(db, request)
    assert (row.status, row.close_reason, row.closed_at) == ("closed", "auto_stale", now)
    assert row.chosen_master_id is not None  # history kept
    for kind in ("review_prompt", "request_cancelled"):
        assert _jobs(db, kind, request.id) == []


def test_c5_leaves_completed_requests_alone(db: Session) -> None:
    request = _chosen(db)
    request_svc.complete_request(db, db.get(User, request.client_id), request.id)
    closed, asked = periodic.stale_requests(db, T + 60 * DAY)
    assert request.id not in closed + asked and _reload(db, request).status == "completed"


# --- a client acting at the same moment wins cleanly ------------------------------------------


@pytest.fixture
def committed_request(migrated_engine):  # type: ignore[no-untyped-def]
    """An open request opened at T (long ago), committed; removed afterwards with its jobs."""
    factory = get_sessionmaker()
    with factory() as s:
        request = _open(s)
        s.commit()
        ids = (request.id, request.client_id)
    yield ids
    with factory() as s:
        s.execute(delete(Job).where(Job.payload["request_id"].as_integer() == ids[0]))
        s.execute(delete(JobRequest).where(JobRequest.id == ids[0]))
        s.execute(delete(User).where(User.id == ids[1]))
        s.commit()


def test_a_client_holding_the_row_wins_over_the_auto_close(committed_request) -> None:  # type: ignore[no-untyped-def]
    request_id, client_id = committed_request
    factory = get_sessionmaker()
    with factory() as client_tx, factory() as worker_tx:
        request_svc.close_request(client_tx, client_tx.get(User, client_id), request_id, "closed")
        closed, _ = periodic.expire_requests(worker_tx, T + 30 * DAY)  # skips the locked row
        worker_tx.commit()
        client_tx.commit()
    assert request_id not in closed
    with factory() as s:
        row = s.get(JobRequest, request_id)
        assert (row.status, row.close_reason) == ("closed", "client_closed")


def test_after_the_auto_close_the_client_gets_a_friendly_state(committed_request) -> None:  # type: ignore[no-untyped-def]
    request_id, client_id = committed_request
    factory = get_sessionmaker()
    with factory() as s:
        closed, _ = periodic.expire_requests(s, T + 30 * DAY)
        s.commit()
    assert request_id in closed
    with factory() as s:
        with pytest.raises(RequestError) as exc:
            request_svc.close_request(s, s.get(User, client_id), request_id, "cancelled")
        assert exc.value.code == "state"
        s.rollback()
        assert s.get(JobRequest, request_id).close_reason == "auto_expired"


# --- schedule ---------------------------------------------------------------------------------


def test_schedule_queues_each_timer_once_per_window(db: Session) -> None:
    now = datetime(2020, 3, 1, 19, 37, 12, tzinfo=UTC)
    periodic.schedule(db, now)
    periodic.schedule(db, now + timedelta(minutes=2))  # same window 19:30-19:40
    periodic.schedule(db, now + timedelta(minutes=3))  # 19:40: the next one
    keys = db.scalars(select(Job.dedupe_key).where(Job.kind.like("periodic:%"))).all()
    stamp = [k for k in keys if k and "20200301T19" in k]
    assert sorted(stamp) == sorted(
        f"{kind}:20200301T{hm}"
        for kind in (periodic.EXPIRY, periodic.STALE, periodic.PURGE)
        for hm in ("1930", "1940")
    )


# --- purge ------------------------------------------------------------------------------------


def _hash() -> str:
    return hash_token(uuid.uuid4().hex)


def test_purge_deletes_only_expired_sign_in_rows(db: Session) -> None:
    now = utcnow()
    user = fs.user(db)

    def session(expires: datetime, seen: datetime) -> AuthSession:
        return AuthSession(
            user_id=user.id, token_hash=_hash(), last_seen_at=seen, expires_at=expires
        )

    def token(expires: datetime) -> AuthToken:
        return AuthToken(user_id=user.id, purpose="tg_link", token_hash=_hash(), expires_at=expires)

    def flow(expires: datetime) -> OAuthFlow:
        return OAuthFlow(state_hash=_hash(), nonce="n", code_verifier="v", expires_at=expires)

    def signin(expires: datetime) -> TelegramSignin:
        return TelegramSignin(token_hash=_hash(), telegram_id=1, first_name="A", expires_at=expires)

    expired = [
        session(now, now),  # absolute limit reached
        session(now + DAY, now - 30 * DAY),  # idle for 30 days
        token(now),
        flow(now - SECOND),
        signin(now - SECOND),
    ]
    kept = [
        session(now + SECOND, now - 30 * DAY + SECOND),
        token(now + SECOND),
        flow(now + SECOND),
        signin(now + SECOND),
    ]
    db.add_all(expired + kept)
    db.flush()
    gone = [(type(r), r.id) for r in expired]
    stay = [(type(r), r.id) for r in kept]
    assert periodic.purge_auth(db, now) >= len(expired)
    db.expire_all()
    assert [db.get(model, i) for model, i in gone] == [None] * len(gone)
    assert all(db.get(model, i) is not None for model, i in stay)
