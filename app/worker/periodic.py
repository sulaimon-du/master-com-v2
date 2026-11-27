"""The worker's timers (spec D6, PRODUCT_RULES C4/C5), the purge of expired sign-in rows
and of finished timer jobs older than a week.

`schedule` queues one job per timer and 10-minute window (dedupe key per window), so several
workers, restarts or a CLI run never repeat a window. Each job's handler does the work in the
job's own transaction.

Ages are measured from the stored UTC timestamps with `timedelta`: "+30 days" means exactly
30 x 24 hours after `opened_at`, never a local calendar day (Review Focus 5).
Rows a client is changing right now are skipped (`SKIP LOCKED`); the next window sees them again,
so a concurrent client action always wins cleanly.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.auth import AuthSession, AuthToken, OAuthFlow, TelegramSignin
from app.models.jobs import PERIODIC_KINDS, PURGE_AUTH, REQUEST_EXPIRY, REQUEST_STALE, Job
from app.models.request import JobRequest
from app.services import jobs
from app.services.auth import SESSION_IDLE

WINDOW = timedelta(minutes=10)
# C4: an open request, from `opened_at` (creation or the last reopen).
EXPIRY_REMIND_AFTER = timedelta(days=27)
EXPIRY_CLOSE_AFTER = timedelta(days=30)
# C5: a request with a chosen master, from `master_chosen_at`.
DONE_ASK_AFTER = timedelta(days=14)
STALE_CLOSE_AFTER = timedelta(days=30)
# Finished timer jobs (one per kind every 10 minutes) are kept this long, then deleted.
TIMER_JOBS_KEPT = timedelta(days=7)

EXPIRY, STALE, PURGE = REQUEST_EXPIRY, REQUEST_STALE, PURGE_AUTH


def window_start(now: datetime) -> datetime:
    """The start of the 10-minute window `now` falls in."""
    return now.replace(minute=now.minute - now.minute % 10, second=0, microsecond=0)


def schedule(db: Session, now: datetime | None = None) -> None:
    """Queue this window's timer jobs (a no-op if they are already queued)."""
    stamp = f"{window_start(now or utcnow()):%Y%m%dT%H%M}"
    for kind in (EXPIRY, STALE, PURGE):
        jobs.enqueue(db, kind, {}, dedupe_key=f"{kind}:{stamp}")


def _lock(*where: Any):  # type: ignore[no-untyped-def]
    return select(JobRequest.id).where(*where).with_for_update(skip_locked=True).scalar_subquery()


def expire_requests(db: Session, now: datetime) -> tuple[list[int], list[int]]:
    """C4: close open requests 30 days old, remind about those 27 days old.

    Returns (closed ids, reminded ids). The close runs first: a request already past 30 days
    (the worker was down) is closed without a late reminder.
    """
    closed = db.scalars(
        update(JobRequest)
        .where(
            JobRequest.id.in_(
                _lock(JobRequest.status == "open", JobRequest.opened_at <= now - EXPIRY_CLOSE_AFTER)
            )
        )
        .values(status="closed", close_reason="auto_expired", closed_at=now)
        .returning(JobRequest.id)
    ).all()
    reminded = db.execute(
        update(JobRequest)
        .where(
            JobRequest.id.in_(
                _lock(
                    JobRequest.status == "open",
                    JobRequest.expiry_reminded_at.is_(None),
                    JobRequest.opened_at <= now - EXPIRY_REMIND_AFTER,
                )
            )
        )
        .values(expiry_reminded_at=now)
        .returning(JobRequest.id, JobRequest.opened_at)
    ).all()
    for request_id, opened_at in reminded:
        # A reopen restarts the clock, so the next cycle needs its own key.
        key = f"expiry_reminder:{request_id}:{int(opened_at.timestamp())}"
        jobs.enqueue(db, "expiry_reminder", {"request_id": request_id}, dedupe_key=key)
    return sorted(closed), sorted(r for r, _ in reminded)


def stale_requests(db: Session, now: datetime) -> tuple[list[int], list[int]]:
    """C5: close chosen requests 30 days old (no review prompt, nobody told); ask at 14 days.

    Returns (closed ids, asked ids).
    """
    closed = db.scalars(
        update(JobRequest)
        .where(
            JobRequest.id.in_(
                _lock(
                    JobRequest.status == "master_chosen",
                    JobRequest.master_chosen_at <= now - STALE_CLOSE_AFTER,
                )
            )
        )
        .values(status="closed", close_reason="auto_stale", closed_at=now)
        .returning(JobRequest.id)
    ).all()
    asked = db.scalars(
        update(JobRequest)
        .where(
            JobRequest.id.in_(
                _lock(
                    JobRequest.status == "master_chosen",
                    JobRequest.done_asked_at.is_(None),
                    JobRequest.master_chosen_at <= now - DONE_ASK_AFTER,
                )
            )
        )
        .values(done_asked_at=now)
        .returning(JobRequest.id)
    ).all()
    for request_id in asked:
        # A master is chosen once per request (no unchoose in v1), so one ask per request.
        jobs.enqueue(
            db, "done_check", {"request_id": request_id}, dedupe_key=f"done_check:{request_id}"
        )
    return sorted(closed), sorted(asked)


def purge_auth(db: Session, now: datetime) -> int:
    """Delete expired sign-in rows: sessions past their absolute or idle limit, one-time links,
    Google sign-in flows and Telegram sign-ins past `expires_at`. Returns the number deleted.
    """
    statements = (
        delete(AuthSession).where(
            (AuthSession.expires_at <= now) | (AuthSession.last_seen_at <= now - SESSION_IDLE)
        ),
        delete(AuthToken).where(AuthToken.expires_at <= now),
        delete(OAuthFlow).where(OAuthFlow.expires_at <= now),
        delete(TelegramSignin).where(TelegramSignin.expires_at <= now),
    )
    return sum(db.execute(s).rowcount for s in statements)  # type: ignore[attr-defined]


def purge_timer_jobs(db: Session, now: datetime) -> int:
    """Delete finished timer jobs older than a week. Notification jobs are never deleted:
    their dedupe keys are what stops a second send.
    """
    finished_before = now - TIMER_JOBS_KEPT
    result = db.execute(
        delete(Job).where(
            Job.kind.in_(PERIODIC_KINDS),
            (Job.done_at <= finished_before) | (Job.failed_at <= finished_before),
        )
    )
    return result.rowcount  # type: ignore[attr-defined, no-any-return]


def _run_expiry(db: Session, payload: dict[str, Any]) -> None:
    expire_requests(db, utcnow())


def _run_stale(db: Session, payload: dict[str, Any]) -> None:
    stale_requests(db, utcnow())


def _run_purge(db: Session, payload: dict[str, Any]) -> None:
    now = utcnow()
    purge_auth(db, now)
    purge_timer_jobs(db, now)


def install() -> None:
    """Register the timer handlers (each reads the clock when it runs)."""
    jobs.register(EXPIRY, _run_expiry)
    jobs.register(STALE, _run_stale)
    jobs.register(PURGE, _run_purge)
