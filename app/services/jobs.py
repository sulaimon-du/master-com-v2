"""Background jobs in Postgres (spec D6).

`enqueue` writes a row in the caller's transaction, so a rolled-back change queues nothing.
The worker (`app.worker`) calls `run_once`: claim due jobs with `FOR UPDATE SKIP LOCKED`, commit
the claim, then run each handler in its own transaction. Plain sync code: no aiogram here.

On staging the worker lives in the bot service (spec D7): after a commit that queued a job, the
site sends a best-effort `GET WORKER_WAKE_URL` from a background thread (no payload, 1 s
timeout, failures ignored), so the worker polls at once and a sleeping service wakes up.
"""

import logging
import os
import socket
import threading
import traceback
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import event, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.time import utcnow
from app.models.jobs import JOB_KINDS, Job

log = logging.getLogger(__name__)

CLAIM_BATCH = 10
# A claim older than this belongs to a worker that died mid-job; another worker may take it.
STALE_LOCK = timedelta(minutes=10)
UNKNOWN_KIND = "unknown_kind"
STALE_GAVE_UP = "stale_lock"
WAKE_TIMEOUT_SECONDS = 1.0
# Session.info flag: this transaction queued at least one job.
_QUEUED = "jobs_queued"


class RetryAfter(Exception):
    """Raised by a handler that must not run again for `seconds` (e.g. Telegram's 429 answer).

    The job is retried after the longer of this and the usual backoff.
    """

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.seconds = max(0.0, float(seconds))


# A handler gets its own session (one transaction) and the job's payload (ids only).
Handler = Callable[[Session, dict[str, Any]], None]
HANDLERS: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    """Name the function that runs jobs of `kind`. Jobs of a kind with no handler fail once."""
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    HANDLERS[kind] = handler


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    run_at: datetime | None = None,
) -> None:
    """Queue `kind` with `payload` (ids only) in the caller's transaction.

    A job with the same `dedupe_key` already queued (or done) makes this a no-op: no error and
    the caller's transaction stays usable (`ON CONFLICT DO NOTHING`).
    """
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    stmt = (
        insert(Job)
        .values(kind=kind, payload=payload, dedupe_key=dedupe_key, run_at=run_at or utcnow())
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    if db.execute(stmt).first() is not None:  # a row was inserted (not a duplicate key)
        db.info[_QUEUED] = True


# --- wake-up ping (staging) -------------------------------------------------------------------

# One ping in flight at a time: a burst of commits never starts a burst of threads.
_ping_in_flight = threading.Lock()


def _ping(url: str) -> None:
    try:
        request = urllib.request.Request(url, method="GET")  # noqa: S310 - http(s) checked
        with urllib.request.urlopen(request, timeout=WAKE_TIMEOUT_SECONDS):  # noqa: S310
            pass
    except Exception as exc:  # best effort: the worker also polls on its own
        log.info("worker wake-up ping failed: %s", type(exc).__name__)
    finally:
        _ping_in_flight.release()


def wake_worker() -> None:
    """Ask the worker to poll now, without waiting for the answer. No-op without a URL."""
    url = get_settings().worker_wake_url
    if not url or not _ping_in_flight.acquire(blocking=False):
        return
    try:
        threading.Thread(target=_ping, args=(url,), name="worker-wake", daemon=True).start()
    except Exception:
        _ping_in_flight.release()
        raise


@event.listens_for(Session, "after_commit")
def _wake_after_commit(session: Session) -> None:
    if not session.info.pop(_QUEUED, False):
        return
    try:
        wake_worker()
    except Exception as exc:  # the change is committed: never fail the request over a ping
        log.info("worker wake-up skipped: %s", type(exc).__name__)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    session.info.pop(_QUEUED, None)


def new_worker_id() -> str:
    """Who holds a claim: host, process and a random part (two loops in one process differ)."""
    return f"{socket.gethostname()[:30]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _pending():  # type: ignore[no-untyped-def]
    return (Job.done_at.is_(None), Job.failed_at.is_(None))


def claim(
    db: Session, worker_id: str, *, now: datetime | None = None, limit: int = CLAIM_BATCH
) -> list[int]:
    """Lock up to `limit` due jobs for `worker_id`; returns their ids in ascending id order.

    The batch is the earliest `run_at` first (then id). Rows another transaction holds (a claim
    in progress, or a job a live worker is running) are skipped, so two workers never take the
    same job and a claim never waits. A claim counts as an attempt (a job that kills its worker
    still runs out of attempts). The caller commits, which keeps `locked_at`/`locked_by`.
    """
    now = now or utcnow()
    due = (
        select(Job.id)
        .where(
            *_pending(),
            Job.run_at <= now,
            Job.attempts < Job.max_attempts,
            (Job.locked_at.is_(None)) | (Job.locked_at <= now - STALE_LOCK),
        )
        .order_by(Job.run_at, Job.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    ids = db.scalars(
        update(Job)
        .where(Job.id.in_(due.scalar_subquery()))
        .values(locked_at=now, locked_by=worker_id, attempts=Job.attempts + 1)
        .returning(Job.id)
    ).all()
    return sorted(ids)


def give_up_stale(db: Session, *, now: datetime | None = None) -> int:
    """Fail jobs whose worker died on their last attempt (they would never be claimed again)."""
    now = now or utcnow()
    # A job still running holds its row lock: skipped, never waited on.
    dead = (
        select(Job.id)
        .where(
            *_pending(),
            Job.locked_at <= now - STALE_LOCK,
            Job.attempts >= Job.max_attempts,
        )
        .with_for_update(skip_locked=True)
    )
    result = db.execute(
        update(Job)
        .where(Job.id.in_(dead.scalar_subquery()))
        .values(failed_at=now, last_error=STALE_GAVE_UP, locked_at=None, locked_by=None)
    )
    return result.rowcount  # type: ignore[attr-defined, no-any-return]


def backoff(attempts: int) -> timedelta:
    """Wait before the next try: 2, 4, 8, 16 minutes after attempts 1, 2, 3, 4 (spec D6)."""
    return timedelta(minutes=2**attempts)


def _lock_mine(db: Session, job_id: int, worker_id: str) -> Job | None:
    """Lock the job for the rest of this transaction if `worker_id` still holds the claim.

    The lock is held through the handler, so other workers' SKIP LOCKED claims pass over a job
    that is running, however long it takes; only a dead connection lets it be reclaimed. A job
    already reclaimed by another worker (or finished), or locked by someone else right now,
    returns None: its handler must not run (a locked row is never waited on).
    """
    return db.scalars(
        select(Job)
        .where(Job.id == job_id, Job.locked_by == worker_id, *_pending())
        .with_for_update(skip_locked=True)
    ).first()


def _run_job(factory: sessionmaker[Session], job_id: int, worker_id: str, now: datetime) -> None:
    with factory() as db:
        job = _lock_mine(db, job_id, worker_id)
        if job is None:
            db.rollback()
            return
        kind, payload = job.kind, dict(job.payload)
        handler = HANDLERS.get(kind)
        if handler is None:
            job.failed_at, job.last_error = now, UNKNOWN_KIND
            db.commit()
            log.error("job %d failed: no handler for kind %s", job_id, kind)
            return
        try:
            handler(db, payload)
            # The handler's changes and "done" commit together, under the same row lock.
            job.done_at, job.last_error = utcnow(), None
            db.commit()
            return
        except Exception as exc:  # the boundary: one job never stops the others
            db.rollback()
            error = type(exc).__name__[:200]
            retry_after = exc.seconds if isinstance(exc, RetryAfter) else 0.0
            # Type and stack frames only, never the message (it may carry ids, SQL values, URLs).
            frames = "".join(traceback.format_tb(exc.__traceback__))
        # The rollback released the lock: take it again and check the claim is still ours.
        job = _lock_mine(db, job_id, worker_id)
        if job is None:
            db.rollback()
            return
        attempts, max_attempts = job.attempts, job.max_attempts
        if attempts >= max_attempts:
            job.failed_at, job.last_error = utcnow(), error
            log.error(
                "job %d %s gave up after %d attempts: %s\n%s",
                job_id,
                kind,
                attempts,
                error,
                frames,
            )
        else:
            wait = max(backoff(attempts), timedelta(seconds=retry_after))
            retry_at = utcnow() + wait
            job.run_at, job.last_error, job.locked_at, job.locked_by = retry_at, error, None, None
            log.warning(
                "job %d %s failed (attempt %d/%d), retry at %s: %s\n%s",
                job_id,
                kind,
                attempts,
                max_attempts,
                f"{retry_at:%Y-%m-%d %H:%M:%S}",
                error,
                frames,
            )
        db.commit()


def run_once(
    factory: sessionmaker[Session],
    worker_id: str,
    *,
    now: datetime | None = None,
    limit: int = CLAIM_BATCH,
) -> int:
    """Claim and run one batch of due jobs; returns how many were claimed."""
    now = now or utcnow()
    with factory() as db:
        give_up_stale(db, now=now)
        ids = claim(db, worker_id, now=now, limit=limit)
        db.commit()
    for job_id in ids:
        try:
            _run_job(factory, job_id, worker_id, now)
        except Exception as exc:  # e.g. the database went away mid-job
            # The claim stays; the job is reclaimed after STALE_LOCK.
            log.error("job %d could not be finished: %s", job_id, type(exc).__name__)
    return len(ids)
