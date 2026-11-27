"""The Postgres job queue and the worker loop (spec D6, D7; Review Focus 4).

Claims and runs commit for real (each job gets its own transaction), so these tests use real
sessions and wipe the jobs table before and after each test. One pytest process at a time.
"""

import threading
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import Job
from app.schemas.requests import ResponseInput
from app.services import jobs
from app.services import responses as response_svc
from app.worker import loop, periodic
from tests import feed_support as fs

WORKER = "test-worker"


@pytest.fixture(autouse=True)
def empty_jobs(migrated_engine) -> Any:  # type: ignore[no-untyped-def]
    def wipe() -> None:
        with get_sessionmaker()() as s:
            s.execute(delete(Job))
            s.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def factory():  # type: ignore[no-untyped-def]
    return get_sessionmaker()


def _add(factory, n: int = 1, kind: str = "new_request", **values: Any) -> list[int]:  # type: ignore[no-untyped-def]
    with factory() as s:
        values.setdefault("run_at", utcnow() - timedelta(seconds=1))
        rows = [Job(kind=kind, payload={"n": i}, **values) for i in range(n)]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def _job(factory, job_id: int) -> Job:  # type: ignore[no-untyped-def]
    with factory() as s:
        job = s.get(Job, job_id)
        assert job is not None
        return job


def _count(db: Session, **where: Any) -> int:
    stmt = select(func.count()).select_from(Job)
    for key, value in where.items():
        stmt = stmt.where(getattr(Job, key) == value)
    return db.scalar(stmt) or 0


# --- enqueue ----------------------------------------------------------------------------------


def test_enqueue_commits_with_the_change(factory) -> None:  # type: ignore[no-untyped-def]
    with factory() as s:
        jobs.enqueue(s, "chosen", {"request_id": 7}, dedupe_key="chosen:7")
        s.commit()
    with factory() as s:
        job = s.scalars(select(Job).where(Job.dedupe_key == "chosen:7")).one()
        assert (job.kind, job.payload, job.attempts, job.max_attempts) == (
            "chosen",
            {"request_id": 7},
            0,
            5,
        )
        assert job.run_at <= utcnow() and job.locked_at is None


def test_enqueue_in_a_rolled_back_transaction_queues_nothing(factory) -> None:  # type: ignore[no-untyped-def]
    with factory() as s:
        jobs.enqueue(s, "chosen", {"request_id": 8}, dedupe_key="chosen:8")
        s.rollback()
    with factory() as s:
        assert _count(s, dedupe_key="chosen:8") == 0


def test_enqueue_twice_with_one_key_is_one_job_and_no_error(db: Session) -> None:
    jobs.enqueue(db, "chosen", {"request_id": 9}, dedupe_key="chosen:9")
    jobs.enqueue(db, "chosen", {"request_id": 9}, dedupe_key="chosen:9")
    # The transaction is still usable (no IntegrityError poisoned it).
    assert _count(db, dedupe_key="chosen:9") == 1


def test_jobs_without_a_key_are_never_deduplicated(db: Session) -> None:
    jobs.enqueue(db, "review_prompt", {"request_id": 1})
    jobs.enqueue(db, "review_prompt", {"request_id": 1})
    assert _count(db, kind="review_prompt", dedupe_key=None) >= 2


def test_enqueue_rejects_an_unknown_kind(db: Session) -> None:
    with pytest.raises(ValueError):
        jobs.enqueue(db, "mystery", {})


def test_respond_withdraw_respond_queues_one_new_response_job(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    request = fs.post(db, city_id=town.id)
    first = response_svc.respond(db, owner, request.id, ResponseInput())
    response_svc.withdraw(db, owner, request.id)
    again = response_svc.respond(db, owner, request.id, ResponseInput())
    assert again.id == first.id
    rows = db.scalars(select(Job).where(Job.kind == "new_response")).all()
    assert [(j.payload, j.dedupe_key) for j in rows if j.payload["response_id"] == first.id] == [
        ({"response_id": first.id}, f"new_response:{first.id}")
    ]
    assert _count(db, dedupe_key=f"new_request:{request.id}") == 1


# --- claim ------------------------------------------------------------------------------------


def test_two_sessions_never_claim_the_same_job(factory) -> None:  # type: ignore[no-untyped-def]
    ids = _add(factory, 3)
    with factory() as a, factory() as b:
        got_a = jobs.claim(a, "worker-a", limit=2)  # still holds its row locks
        got_b = jobs.claim(b, "worker-b", limit=10)  # skips them instead of waiting
        a.commit()
        b.commit()
    assert got_a == ids[:2] and got_b == ids[2:]
    with factory() as c:  # all three are freshly claimed now
        assert jobs.claim(c, "worker-c") == []
        c.commit()
    assert [_job(factory, i).locked_by for i in ids] == ["worker-a", "worker-a", "worker-b"]
    assert [_job(factory, i).attempts for i in ids] == [1, 1, 1]


def test_claim_takes_only_due_pending_jobs(factory) -> None:  # type: ignore[no-untyped-def]
    now = utcnow()
    (due,) = _add(factory)
    (later,) = _add(factory, run_at=now + timedelta(minutes=1))
    (done,) = _add(factory, done_at=now)
    (failed,) = _add(factory, failed_at=now)
    (spent,) = _add(factory, attempts=5)
    with factory() as s:
        assert jobs.claim(s, WORKER, now=now + timedelta(seconds=1)) == [due]
        s.commit()
    del later, done, failed, spent


def test_a_stale_lock_is_reclaimable_a_fresh_one_is_not(factory) -> None:  # type: ignore[no-untyped-def]
    now = utcnow()
    (stale,) = _add(factory, locked_at=now - timedelta(minutes=10), locked_by="dead", attempts=1)
    (fresh,) = _add(
        factory, locked_at=now - timedelta(minutes=9, seconds=59), locked_by="busy", attempts=1
    )
    with factory() as s:
        assert jobs.claim(s, WORKER, now=now) == [stale]
        s.commit()
    job = _job(factory, stale)
    assert (job.locked_by, job.attempts) == (WORKER, 2)
    assert _job(factory, fresh).locked_by == "busy"


def test_a_job_whose_worker_died_on_the_last_attempt_fails(factory) -> None:  # type: ignore[no-untyped-def]
    now = utcnow()
    (job_id,) = _add(factory, locked_at=now - timedelta(minutes=11), locked_by="dead", attempts=5)
    assert jobs.run_once(factory, WORKER, now=now) == 0
    job = _job(factory, job_id)
    assert job.failed_at is not None and job.last_error == jobs.STALE_GAVE_UP


# --- run_once ---------------------------------------------------------------------------------


@pytest.fixture
def handled(monkeypatch) -> list[dict]:  # type: ignore[no-untyped-def]
    """A handler for "new_request" that records payloads and fails on {"fail": true}."""
    seen: list[dict] = []

    def handler(db: Session, payload: dict) -> None:
        seen.append(payload)
        if payload.get("fail"):
            # Its writes must roll back with it.
            jobs.enqueue(db, "chosen", {"request_id": 1}, dedupe_key="written-then-failed")
            raise RuntimeError("boom with details that must not be stored")

    monkeypatch.setitem(jobs.HANDLERS, "new_request", handler)
    return seen


def test_a_handled_job_is_done_with_its_effects(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(db: Session, payload: dict) -> None:
        jobs.enqueue(db, "chosen", {"request_id": payload["n"]}, dedupe_key="follow-up")

    monkeypatch.setitem(jobs.HANDLERS, "new_request", handler)
    (job_id,) = _add(factory)
    assert jobs.run_once(factory, WORKER) == 1
    job = _job(factory, job_id)
    assert job.done_at is not None and job.failed_at is None and job.attempts == 1
    with factory() as s:
        assert _count(s, dedupe_key="follow-up") == 1


def test_a_failing_handler_retries_later_and_others_continue(factory, handled) -> None:  # type: ignore[no-untyped-def]
    ok1, bad, ok2 = (_add(factory)[0], _add(factory)[0], _add(factory)[0])
    with factory() as s:
        s.get(Job, bad).payload = {"fail": True}
        s.commit()
    before = utcnow()
    assert jobs.run_once(factory, WORKER) == 3
    assert len(handled) == 3
    assert _job(factory, ok1).done_at is not None and _job(factory, ok2).done_at is not None
    job = _job(factory, bad)
    assert job.done_at is None and job.failed_at is None
    assert (job.attempts, job.last_error, job.locked_at, job.locked_by) == (
        1,
        "RuntimeError",
        None,
        None,
    )
    assert before + timedelta(minutes=2) <= job.run_at <= utcnow() + timedelta(minutes=2)
    with factory() as s:
        assert _count(s, dedupe_key="written-then-failed") == 0
    # Not due yet: nothing to claim.
    assert jobs.run_once(factory, WORKER) == 0


def test_backoff_doubles_and_the_job_fails_after_max_attempts(factory, handled) -> None:  # type: ignore[no-untyped-def]
    (job_id,) = _add(factory)
    with factory() as s:
        s.get(Job, job_id).payload = {"fail": True}
        s.commit()
    later = utcnow()
    for attempt in range(1, 6):
        later += timedelta(days=1)  # past any backoff
        started = utcnow()
        assert jobs.run_once(factory, WORKER, now=later) == 1
        job = _job(factory, job_id)
        assert job.attempts == attempt
        if attempt < 5:
            wait = job.run_at - started
            assert timedelta(minutes=2**attempt) <= wait < timedelta(minutes=2**attempt, seconds=5)
            assert job.failed_at is None
    assert job.failed_at is not None and job.last_error == "RuntimeError"
    assert jobs.run_once(factory, WORKER, now=later + timedelta(days=30)) == 0
    assert len(handled) == 5


def test_an_unknown_kind_fails_once_without_stopping_the_batch(factory, handled) -> None:  # type: ignore[no-untyped-def]
    (mystery,) = _add(factory, kind="no_such_kind")
    (ok,) = _add(factory)
    assert jobs.run_once(factory, WORKER) == 2
    job = _job(factory, mystery)
    assert job.failed_at is not None and job.last_error == jobs.UNKNOWN_KIND
    assert _job(factory, ok).done_at is not None
    assert jobs.run_once(factory, WORKER, now=utcnow() + timedelta(days=1)) == 0


def test_a_reclaimed_job_is_not_finished_by_the_old_worker(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (job_id,) = _add(factory)

    def slow(db: Session, payload: dict) -> None:
        # Meanwhile another worker took the job over (this one looked dead).
        with factory() as other:
            other.get(Job, job_id).locked_by = "new-holder"
            other.commit()

    monkeypatch.setitem(jobs.HANDLERS, "new_request", slow)
    assert jobs.run_once(factory, WORKER) == 1
    job = _job(factory, job_id)
    assert job.done_at is None and job.locked_by == "new-holder"


def test_register_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        jobs.register("mystery", lambda db, payload: None)


# --- the loop ---------------------------------------------------------------------------------


def test_the_loop_survives_a_crashing_iteration_and_stops_on_request(
    factory, monkeypatch, handled
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(periodic, "schedule", lambda db, now=None: None)
    stop = threading.Event()
    calls: list[str] = []
    real = jobs.run_once

    def flaky(f, worker_id, **kw):  # type: ignore[no-untyped-def]
        calls.append("call")
        if len(calls) == 1:
            raise ConnectionError("database went away")
        claimed = real(f, worker_id, **kw)
        if handled:
            stop.set()
        return claimed

    monkeypatch.setattr(jobs, "run_once", flaky)
    _add(factory)
    thread = threading.Thread(
        target=loop.run_loop,
        args=(stop,),
        kwargs={"factory": factory, "poll_seconds": 0.01, "error_pause_seconds": 0.01},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(calls) >= 2 and handled == [{"n": 0}]


def test_the_loop_queues_the_timers_once_per_window(factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    windows: list[object] = []
    monkeypatch.setattr(periodic, "schedule", lambda db, now=None: windows.append(now))
    monkeypatch.setattr(jobs, "run_once", lambda f, worker_id, **kw: 0)
    fixed = utcnow()
    monkeypatch.setattr(loop, "utcnow", lambda: fixed)
    window = None
    for _ in range(3):
        claimed, window = loop.tick(factory, WORKER, window)
        assert claimed == 0
    assert len(windows) == 1


def test_cli_worker_run_once_runs_every_due_job(factory, monkeypatch, handled, capsys) -> None:  # type: ignore[no-untyped-def]
    from app import cli

    scheduled: list[object] = []
    monkeypatch.setattr(periodic, "schedule", lambda db, now=None: scheduled.append(now))
    _add(factory, 3)
    assert cli.main(["worker", "run-once"]) == 0
    assert capsys.readouterr().out.strip() == "3 jobs run"
    assert len(scheduled) == 1 and len(handled) == 3
