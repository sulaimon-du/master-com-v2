"""The worker loop as a daemon thread of another process: the bot service on staging (spec D7).

Render's free plan has no background workers, so with `RUN_WORKER=1` the bot runs the loop next
to its webhook server. The thread never takes the bot down: a loop that raises or returns is
restarted after a pause, and a thread that died anyway is started again on the next wake-up.
Plain threading: no aiogram here.
"""

import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any

from app.worker.loop import run_loop

log = logging.getLogger("app.worker")

RESTART_PAUSE_SECONDS = 30.0
SHUTDOWN_SECONDS = 10.0

Loop = Callable[..., None]


class BackgroundWorker:
    """Runs `run_loop` in a supervised daemon thread; `wake()` makes it poll now."""

    def __init__(
        self,
        *,
        loop: Loop = run_loop,
        restart_pause_seconds: float = RESTART_PAUSE_SECONDS,
        **loop_kwargs: Any,
    ) -> None:
        self._loop = loop
        self._loop_kwargs = loop_kwargs
        self._restart_pause = restart_pause_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()
        self.restarts = 0

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._guard:
            if self._stop.is_set() or self.alive:
                return
            self._thread = threading.Thread(target=self._supervise, name="worker", daemon=True)
            self._thread.start()

    def wake(self) -> None:
        """Poll for jobs now (a wake-up ping arrived); restart a thread that died."""
        if not self._stop.is_set() and not self.alive:
            log.error("worker thread was not running; starting it again")
            self.start()
        self._wake.set()

    def shutdown(self, timeout: float = SHUTDOWN_SECONDS) -> None:
        """Stop the loop and wait for the job in progress (a job cut off is retried later)."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                self._loop(self._stop, wake=self._wake, **self._loop_kwargs)
            except Exception as exc:  # the thread's boundary: the bot keeps serving
                # Type and stack frames only, never the message (it may quote data).
                frames = "".join(traceback.format_tb(exc.__traceback__))
                log.error("worker loop crashed: %s\n%s", type(exc).__name__, frames)
            if self._stop.is_set():
                break
            self.restarts += 1
            log.warning("worker loop restarting in %.0f s", self._restart_pause)
            self._stop.wait(self._restart_pause)
