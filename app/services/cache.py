"""A tiny in-process TTL cache (spec D6): one value per key, recomputed after `seconds`.

Process-local on purpose (no Redis in v1): each worker keeps its own copy, so a change shows up
within one TTL everywhere. The clock is module-level so tests can move it.
"""

import threading
import time
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_entries: dict[Any, tuple[float, Any]] = {}


def now() -> float:
    return time.monotonic()


def get_or_compute[T](key: Any, seconds: float, compute: Callable[[], T]) -> T:
    """The cached value for `key` while it is younger than `seconds`, else `compute()`.

    Computing happens outside the lock (a slow query must not block other keys); two
    simultaneous misses may both compute, and the last one stored wins.
    """
    with _lock:
        hit = _entries.get(key)
        if hit is not None and now() - hit[0] < seconds:
            return hit[1]
    value = compute()
    with _lock:
        _entries[key] = (now(), value)
    return value


def clear() -> None:
    with _lock:
        _entries.clear()
