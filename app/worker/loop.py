"""The worker loop. Started by `python -m app.worker`, and on staging in a daemon thread of the
bot service (Task 9), so it is a plain function that runs until `stop` is set.
"""

import logging
import threading
import traceback
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.services import jobs, notifications
from app.worker import periodic

log = logging.getLogger("app.worker")

POLL_SECONDS = 5.0
# After an iteration crashed (say, the database is down), wait longer before trying again.
ERROR_PAUSE_SECONDS = 30.0
# A wake-up never makes the loop poll more often than this (anyone can ping the bot's /health).
WAKE_MIN_GAP_SECONDS = 1.0


def install_handlers() -> None:
    """Every handler the worker runs: the timers and the notifications."""
    periodic.install()
    notifications.install()


def tick(
    factory: sessionmaker[Session], worker_id: str, last_window: datetime | None
) -> tuple[int, datetime]:
    """One iteration: queue this window's timers (once per window), then run one batch.

    Returns (jobs claimed, the window whose timers are queued).
    """
    window = periodic.window_start(utcnow())
    if window != last_window:
        with factory() as db:
            periodic.schedule(db, window)
            db.commit()
    return jobs.run_once(factory, worker_id), window


def _wait(
    stop: threading.Event, wake: threading.Event | None, seconds: float, min_gap: float
) -> None:
    if wake is None:
        stop.wait(seconds)
        return
    if wake.wait(seconds):
        # Cleared before the next poll, so a job committed after this point wakes us again.
        wake.clear()
        stop.wait(min_gap)


def run_loop(
    stop: threading.Event,
    *,
    factory: sessionmaker[Session] | None = None,
    poll_seconds: float = POLL_SECONDS,
    error_pause_seconds: float = ERROR_PAUSE_SECONDS,
    wake: threading.Event | None = None,
    wake_min_gap_seconds: float = WAKE_MIN_GAP_SECONDS,
) -> None:
    """Run batches until `stop` is set. An exception never ends the loop: it is logged and the
    next iteration starts after a pause. A full batch means more may be due: no wait then.

    `wake` (set by a wake-up ping, staging) ends the wait between polls early. Whoever sets
    `stop` from outside the loop also sets `wake`, so a waiting loop stops at once.
    """
    install_handlers()
    factory = factory or get_sessionmaker()
    worker_id = jobs.new_worker_id()
    log.info("worker started")
    window: datetime | None = None
    while not stop.is_set():
        try:
            claimed, window = tick(factory, worker_id, window)
        except Exception as exc:  # the per-iteration boundary
            frames = "".join(traceback.format_tb(exc.__traceback__))
            log.error("worker iteration failed: %s\n%s", type(exc).__name__, frames)
            stop.wait(error_pause_seconds)
            continue
        if claimed < jobs.CLAIM_BATCH:
            _wait(stop, wake, poll_seconds, wake_min_gap_seconds)
    log.info("worker stopped")
