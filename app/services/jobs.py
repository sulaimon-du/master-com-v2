"""Background jobs in Postgres (spec D6).

`enqueue` writes a row in the caller's transaction, so a rolled-back change queues nothing.
The worker (`app.worker`) calls `run_once`: claim due jobs with `FOR UPDATE SKIP LOCKED`, commit
the claim, then run each handler in its own transaction. Plain sync code: no aiogram here.
"""

import logging
import os
import socket
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utcnow
from app.models.jobs import JOB_KINDS, Job

log = logging.getLogger(__name__)

CLAIM_BATCH = 10
# A claim older than this belongs to a worker that died mid-job; another worker may take it.
STALE_LOCK = timedelta(minutes=10)
UNKNOWN_KIND = "unknown_kind"
STALE_GAVE_UP = "stale_lock"

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
    )
    db.execute(stmt)


def new_worker_id() -> str:
    """Who holds a claim: host, process and a random part (two loops in one process differ)."""
    return f"{socket.gethostname()[:30]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _pending():  # type: ignore[no-untyped-def]
    return (Job.done_at.is_(None), Job.failed_at.is_(None))


def claim(
    db: Session, worker_id: str, *, now: datetime | None = None, limit: int = CLAIM_BATCH
) -> list[int]:
    """Lock up to `limit` due jobs for `worker_id` and return their ids, oldest first.

    Rows another transaction is claiming are skipped, so two workers never take the same job.
    A claim counts as an attempt (a job that kills its worker still runs out of attempts).
    The caller commits, which releases the row locks but keeps `locked_at`/`locked_by`.
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
    result = db.execute(
        update(Job)
        .where(
            *_pending(),
            Job.locked_at <= now - STALE_LOCK,
            Job.attempts >= Job.max_attempts,
        )
        .values(failed_at=now, last_error=STALE_GAVE_UP, locked_at=None, locked_by=None)
    )
    return result.rowcount  # type: ignore[attr-defined, no-any-return]


def backoff(attempts: int) -> timedelta:
    """Wait before the next try: 2, 4, 8, 16 minutes after attempts 1, 2, 3, 4 (spec D6)."""
    return timedelta(minutes=2**attempts)


def _mine(job_id: int, worker_id: str):  # type: ignore[no-untyped-def]
    # Only the current claim holder may finish a job (a reclaimed job has a new holder).
    return update(Job).where(Job.id == job_id, Job.locked_by == worker_id, *_pending())


def _run_job(factory: sessionmaker[Session], job_id: int, worker_id: str, now: datetime) -> None:
    with factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.locked_by != worker_id:
            return
        kind, payload, attempts, max_attempts = (
            job.kind,
            dict(job.payload),
            job.attempts,
            job.max_attempts,
        )
        handler = HANDLERS.get(kind)
        if handler is None:
            db.execute(_mine(job_id, worker_id).values(failed_at=now, last_error=UNKNOWN_KIND))
            db.commit()
            log.error("job %d failed: no handler for kind %s", job_id, kind)
            return
        try:
            handler(db, payload)
            # The handler's changes and "done" commit together.
            db.execute(_mine(job_id, worker_id).values(done_at=utcnow(), last_error=None))
            db.commit()
            return
        except Exception as exc:  # the boundary: one job never stops the others
            db.rollback()
            error = type(exc).__name__[:200]
            # Type and stack frames only, never the message (it may carry ids, SQL values, URLs).
            frames = "".join(traceback.format_tb(exc.__traceback__))
        if attempts >= max_attempts:
            values: dict[str, Any] = {"failed_at": utcnow(), "last_error": error}
            log.error(
                "job %d %s gave up after %d attempts: %s\n%s",
                job_id,
                kind,
                attempts,
                error,
                frames,
            )
        else:
            retry_at = utcnow() + backoff(attempts)
            values = {"run_at": retry_at, "last_error": error, "locked_at": None, "locked_by": None}
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
        db.execute(_mine(job_id, worker_id).values(**values))
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
