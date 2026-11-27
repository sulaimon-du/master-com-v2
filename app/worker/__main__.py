"""`python -m app.worker`: the worker as its own process (local compose, production)."""

import logging
import signal
import threading

from app.core.config import get_settings
from app.core.monitoring import init_sentry
from app.worker.loop import run_loop


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_sentry(settings, component="worker")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_loop(stop)


if __name__ == "__main__":
    main()
