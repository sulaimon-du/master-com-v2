"""The worker inside the bot service on staging (spec D7; Review Focus 4) and the wake-up ping.

Some tests commit for real (the ping fires only after a real commit), so they wipe the jobs
table before and after. One pytest process at a time.
"""

import asyncio
import itertools
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.bot import runner
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import utcnow
from app.models import Job
from app.services import jobs
from app.worker import background, loop, periodic

FAKE_TOKEN = "123456:TEST-token-not-real"


@pytest.fixture
def empty_jobs(migrated_engine) -> Iterator[None]:  # type: ignore[no-untyped-def]
    def wipe() -> None:
        with get_sessionmaker()() as s:
            s.execute(delete(Job))
            s.commit()

    wipe()
    yield
    wipe()


def _wait_for(predicate, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- the bot runner starts the thread only with RUN_WORKER=1 ----------------------------------


class _FakeWorker:
    instances: ClassVar[list["_FakeWorker"]] = []

    def __init__(self) -> None:
        self.started = self.stopped = False
        self.woken = 0
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.started = True

    def wake(self) -> None:
        self.woken += 1

    def shutdown(self) -> None:
        self.stopped = True


@pytest.fixture
def staging_bot(monkeypatch):  # type: ignore[no-untyped-def]
    """`runner.main()` in webhook mode with nothing real: no Bot, no server, no thread."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://bot.example.com/telegram/webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret-value")
    monkeypatch.setattr(runner, "Bot", lambda token: object())
    built: dict[str, Any] = {}

    def fake_app(bot, dp, secret, *, on_health=None):  # type: ignore[no-untyped-def]
        built["on_health"] = on_health
        return "app"

    served: list[bool] = []

    def fake_run_app(app, **kwargs):  # type: ignore[no-untyped-def]
        # While the bot serves, the worker is already running.
        served.append(all(w.started and not w.stopped for w in _FakeWorker.instances))

    monkeypatch.setattr(runner, "build_webhook_app", fake_app)
    monkeypatch.setattr(runner.web, "run_app", fake_run_app)
    monkeypatch.setattr(runner, "BackgroundWorker", _FakeWorker)
    _FakeWorker.instances = []
    get_settings.cache_clear()
    yield monkeypatch, built, served
    get_settings.cache_clear()


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_the_bot_runs_no_worker_without_run_worker(staging_bot, value: str) -> None:
    monkeypatch, built, served = staging_bot
    monkeypatch.setenv("RUN_WORKER", value)
    get_settings.cache_clear()
    runner.main()
    assert _FakeWorker.instances == []
    assert built["on_health"] is None
    assert served == [True]


def test_the_bot_starts_wakes_and_stops_the_worker_with_run_worker(staging_bot) -> None:
    monkeypatch, built, served = staging_bot
    monkeypatch.setenv("RUN_WORKER", "1")
    get_settings.cache_clear()
    runner.main()
    [worker] = _FakeWorker.instances
    assert served == [True]  # started before serving
    assert worker.stopped  # stopped cleanly when the server returned
    built["on_health"]()
    assert worker.woken == 1


def test_the_worker_stops_even_when_the_server_fails(staging_bot) -> None:
    monkeypatch, _built, _served = staging_bot
    monkeypatch.setenv("RUN_WORKER", "1")

    def broken(app, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("port in use")

    monkeypatch.setattr(runner.web, "run_app", broken)
    get_settings.cache_clear()
    with pytest.raises(OSError):
        runner.main()
    [worker] = _FakeWorker.instances
    assert worker.stopped


def test_health_wakes_the_worker_and_stays_ok_when_the_wake_fails() -> None:
    from aiogram import Bot
    from aiohttp.test_utils import TestClient, TestServer

    calls: list[str] = []

    def failing_wake() -> None:
        calls.append("wake")
        raise RuntimeError("worker is broken")

    async def scenario() -> list[int]:
        bot = Bot(FAKE_TOKEN)
        app = runner.build_webhook_app(
            bot, runner.build_dispatcher(), "s3cret-value", on_health=failing_wake
        )
        async with TestClient(TestServer(app)) as client:
            return [(await client.get("/health")).status for _ in range(2)]

    assert asyncio.run(scenario()) == [200, 200]
    assert calls == ["wake", "wake"]


# --- the supervised thread (Review Focus 4) ---------------------------------------------------


def test_a_crashing_loop_is_restarted_and_stops_on_shutdown(caplog) -> None:  # type: ignore[no-untyped-def]
    runs: list[threading.Event] = []

    def crashing_then_running(stop: threading.Event, *, wake: threading.Event) -> None:
        runs.append(wake)
        if len(runs) == 1:
            raise RuntimeError("secret detail +" + str(992_900_000_000))
        stop.wait()

    worker = background.BackgroundWorker(loop=crashing_then_running, restart_pause_seconds=0.01)
    worker.start()
    assert _wait_for(lambda: len(runs) == 2)
    assert worker.alive and worker.restarts == 1
    worker.shutdown(timeout=5)
    assert not worker.alive
    crash = [r for r in caplog.records if "crashed" in r.getMessage()]
    assert crash and "RuntimeError" in crash[0].getMessage()
    # Type only, never the message (it may quote data).
    assert all("+992900000000" not in r.getMessage() for r in caplog.records)


def test_a_loop_that_returns_is_restarted_too() -> None:
    runs: list[int] = []

    def returns(stop: threading.Event, *, wake: threading.Event) -> None:
        runs.append(1)
        if len(runs) >= 3:
            stop.wait()

    worker = background.BackgroundWorker(loop=returns, restart_pause_seconds=0.01)
    worker.start()
    assert _wait_for(lambda: len(runs) == 3)
    worker.shutdown(timeout=5)
    assert not worker.alive


# The SystemExit that ends the thread is reported by pytest's thread hook; that is the point.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_dead_thread_is_started_again_on_wake() -> None:
    runs: list[int] = []

    def dies(stop: threading.Event, *, wake: threading.Event) -> None:
        runs.append(1)
        if len(runs) == 1:
            raise SystemExit  # not an Exception: the supervisor itself ends
        stop.wait()

    worker = background.BackgroundWorker(loop=dies, restart_pause_seconds=0.01)
    worker.start()
    assert _wait_for(lambda: not worker.alive)
    worker.wake()
    assert _wait_for(lambda: len(runs) == 2 and worker.alive)
    worker.shutdown(timeout=5)
    assert not worker.alive


def test_after_shutdown_a_wake_starts_nothing() -> None:
    runs: list[int] = []

    def forever(stop: threading.Event, *, wake: threading.Event) -> None:
        runs.append(1)
        stop.wait()

    worker = background.BackgroundWorker(loop=forever)
    worker.start()
    assert _wait_for(lambda: runs == [1])
    worker.shutdown(timeout=5)
    worker.wake()
    worker.start()
    time.sleep(0.05)
    assert runs == [1] and not worker.alive


def test_a_wake_makes_the_real_loop_poll_now(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    polls: list[float] = []
    monkeypatch.setattr(loop, "install_handlers", lambda: None)
    monkeypatch.setattr(loop, "tick", lambda f, w, last: (polls.append(1) or 0, last))
    worker = background.BackgroundWorker(
        factory=object(), poll_seconds=60, error_pause_seconds=60, wake_min_gap_seconds=0
    )
    worker.start()
    assert _wait_for(lambda: len(polls) == 1)
    time.sleep(0.05)
    assert len(polls) == 1  # waiting 60 s for the next poll
    worker.wake()
    assert _wait_for(lambda: len(polls) == 2)
    started = time.monotonic()
    worker.shutdown(timeout=5)
    assert not worker.alive and time.monotonic() - started < 2  # no 60 s wait on shutdown


def test_wakes_never_poll_more_often_than_the_minimum_gap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    polls: list[float] = []
    monkeypatch.setattr(loop, "install_handlers", lambda: None)
    monkeypatch.setattr(
        loop, "tick", lambda f, w, last: (polls.append(time.monotonic()) or 0, last)
    )
    stop, wake = threading.Event(), threading.Event()
    thread = threading.Thread(
        target=loop.run_loop,
        args=(stop,),
        kwargs={
            "factory": object(),
            "poll_seconds": 60,
            "wake": wake,
            "wake_min_gap_seconds": 0.2,
        },
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline:  # a flood of wake-ups
        wake.set()
        time.sleep(0.005)
    stop.set()
    wake.set()
    thread.join(timeout=5)
    gaps = [b - a for a, b in itertools.pairwise(polls)]
    assert 2 <= len(polls) <= 5
    assert all(gap >= 0.19 for gap in gaps)


# --- the wake-up ping ---------------------------------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    hits: ClassVar[list[dict[str, Any]]] = []
    delay = 0.0

    def do_GET(self) -> None:
        time.sleep(type(self).delay)
        type(self).hits.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.headers.get("Content-Length"),
            }
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def wake_server(monkeypatch) -> Iterator[type[_Recorder]]:  # type: ignore[no-untyped-def]
    _Recorder.hits, _Recorder.delay = [], 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("WORKER_WAKE_URL", f"http://127.0.0.1:{server.server_port}/health")
    get_settings.cache_clear()
    yield _Recorder
    server.shutdown()
    server.server_close()
    get_settings.cache_clear()
    # Let a ping still in flight release its slot before the next test.
    _wait_for(lambda: not jobs._ping_in_flight.locked())


def _enqueue_new(db: Session, key: str | None = None) -> None:
    jobs.enqueue(db, "new_request", {"request_id": 0}, dedupe_key=key)


def test_the_ping_fires_after_a_commit_that_queued_a_job(wake_server, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    with get_sessionmaker()() as db:
        _enqueue_new(db)
        time.sleep(0.2)
        assert wake_server.hits == []  # not before the commit
        db.commit()
    assert _wait_for(lambda: len(wake_server.hits) == 1)
    [hit] = wake_server.hits
    assert hit["path"] == "/health"
    # Nothing sensitive: no body, no cookies, no auth, no ids.
    assert hit["body"] is None
    names = {name.lower() for name in hit["headers"]}
    assert not names & {"cookie", "authorization", "x-telegram-bot-api-secret-token"}


def test_no_ping_after_a_rollback_or_a_commit_without_jobs(wake_server, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    with get_sessionmaker()() as db:
        _enqueue_new(db)
        db.rollback()
        db.commit()  # the rollback forgot the queued job
        db.execute(select(1))
        db.commit()
    with get_sessionmaker()() as db:
        _enqueue_new(db)
    # closed without a commit
    with get_sessionmaker()() as db:
        db.commit()
    time.sleep(0.3)
    assert wake_server.hits == []


def test_no_ping_when_the_dedupe_key_already_exists(wake_server, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    with get_sessionmaker()() as db:
        _enqueue_new(db, "same-key")
        db.commit()
    assert _wait_for(lambda: len(wake_server.hits) == 1)
    with get_sessionmaker()() as db:
        _enqueue_new(db, "same-key")  # ON CONFLICT DO NOTHING: nothing new to run
        db.commit()
    time.sleep(0.3)
    assert len(wake_server.hits) == 1


def test_a_slow_or_dead_wake_url_never_blocks_the_commit(wake_server, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    wake_server.delay = 3.0
    with get_sessionmaker()() as db:
        _enqueue_new(db)
        started = time.monotonic()
        db.commit()
        assert time.monotonic() - started < 0.5
    # The ping gives up after its 1 s timeout and frees its slot.
    assert _wait_for(lambda: not jobs._ping_in_flight.locked(), timeout=3)


def test_a_broken_ping_never_fails_the_commit(monkeypatch, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    def broken() -> None:
        raise RuntimeError("no threads left")

    monkeypatch.setattr(jobs, "wake_worker", broken)
    with get_sessionmaker()() as db:
        _enqueue_new(db)
        db.commit()
    with get_sessionmaker()() as db:
        assert db.scalars(select(Job)).all()


def test_a_burst_of_commits_sends_at_most_one_ping_at_a_time(wake_server, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    wake_server.delay = 0.3
    for _ in range(5):
        with get_sessionmaker()() as db:
            _enqueue_new(db)
            db.commit()
    assert _wait_for(lambda: not jobs._ping_in_flight.locked(), timeout=3)
    assert len(wake_server.hits) == 1


def test_no_wake_url_no_ping(monkeypatch, empty_jobs) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("WORKER_WAKE_URL", raising=False)
    get_settings.cache_clear()
    pinged: list[str] = []
    monkeypatch.setattr(jobs, "_ping", pinged.append)
    with get_sessionmaker()() as db:
        _enqueue_new(db)
        db.commit()
    get_settings.cache_clear()
    time.sleep(0.1)
    assert pinged == []
    assert not jobs._ping_in_flight.locked()


def test_a_web_request_that_queues_a_job_pings_after_its_commit(
    wake_server, empty_jobs, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The site's real transaction boundary (get_db) commits, then pings."""
    from app.core.db import get_db

    gen = get_db()
    db = next(gen)
    _enqueue_new(db)
    time.sleep(0.1)
    assert wake_server.hits == []
    with pytest.raises(StopIteration):
        next(gen)  # the dependency's teardown commits
    assert _wait_for(lambda: len(wake_server.hits) == 1)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://bot.example.com/health",
        "https://user:pw@bot.example.com/health",
        "https://b.example/h?x=1",
    ],
)
def test_the_wake_url_is_a_plain_http_url(monkeypatch, url: str) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    monkeypatch.setenv("WORKER_WAKE_URL", url)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError) as exc:
            get_settings()
    finally:
        get_settings.cache_clear()
    assert "WORKER_WAKE_URL" in str(exc.value).upper()
    assert "pw@" not in str(exc.value)


# --- jobs queued before the worker ran (T7 review M7) -----------------------------------------


def test_notification_jobs_queued_before_the_worker_ran_are_processed(  # type: ignore[no-untyped-def]
    monkeypatch, empty_jobs
) -> None:
    """Jobs wait in the table while no worker runs; the first worker runs them with the real
    handlers (Task 8), so none fails as an unknown kind."""
    factory = get_sessionmaker()
    monkeypatch.setattr(periodic, "schedule", lambda db, now=None: None)
    with factory() as db:
        for kind in ("new_request", "new_response", "chosen", "request_cancelled"):
            jobs.enqueue(
                db, kind, {"request_id": 0, "response_id": 0}, run_at=utcnow() - timedelta(hours=1)
            )
        db.commit()
    stop = threading.Event()
    calls: list[int] = []
    real = jobs.run_once

    def once_then_stop(f, worker_id, **kw):  # type: ignore[no-untyped-def]
        calls.append(1)
        claimed = real(f, worker_id, **kw)
        stop.set()
        return claimed

    monkeypatch.setattr(jobs, "run_once", once_then_stop)
    loop.run_loop(stop, factory=factory, poll_seconds=0.01)
    with factory() as db:
        rows = db.scalars(select(Job)).all()
    assert len(rows) == 4
    assert all(r.done_at is not None and r.failed_at is None for r in rows), [
        (r.kind, r.last_error) for r in rows
    ]
