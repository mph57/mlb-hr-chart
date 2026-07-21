"""Tiny in-process TTL cache for assembled boards.

Building a board hits several live endpoints and takes tens of seconds, so we
cache the result per date. One gunicorn worker on a small host is the target;
if you scale to multiple workers, swap this for Redis.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_LOCK = threading.Lock()
_STORE: dict[str, tuple[float, Any]] = {}


def get_or_build(key: str, ttl: int, builder: Callable[[], Any]) -> Any:
    """Return a cached value for ``key`` if fresh, else build, store, return."""
    now = time.time()
    with _LOCK:
        hit = _STORE.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    # Build outside the lock so slow network work doesn't block other dates.
    value = builder()
    with _LOCK:
        _STORE[key] = (time.time(), value)
    return value


def invalidate(key: str | None = None) -> None:
    with _LOCK:
        if key is None:
            _STORE.clear()
        else:
            _STORE.pop(key, None)
