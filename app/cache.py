"""TTL cache for assembled boards, with stale-while-revalidate + disk backing.

Building a board hits several live endpoints and takes tens of seconds (well
over a minute on a cold free-tier box), so we never want a visitor to wait on a
full rebuild. This cache guarantees that:

  * A **fresh** value (age < ttl) is returned straight from memory.
  * A **stale** value is returned *immediately* while a single background thread
    rebuilds it (stale-while-revalidate) — nobody blocks on the slow path once a
    board exists.
  * Values are mirrored to **disk** (``instance/board_cache/``) so a process
    restart or free-tier spin-down doesn't throw the last board away; the next
    cold request serves the on-disk snapshot instantly and refreshes in the
    background.

Only the very first build ever — no memory value and no disk snapshot — blocks
the request. One gunicorn worker on a small host is the target; if you scale to
multiple workers, swap this for Redis.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

_LOCK = threading.Lock()
_STORE: dict[str, tuple[float, Any]] = {}
_INFLIGHT: set[str] = set()          # keys with a background rebuild in progress

_DISK_DIR = Path(__file__).resolve().parents[1] / "instance" / "board_cache"


# --------------------------------------------------------------------------- #
# disk snapshot helpers (best-effort; never let disk issues break a request)
# --------------------------------------------------------------------------- #
def _disk_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return _DISK_DIR / f"{safe}.json"


def _disk_load(key: str) -> tuple[float, Any] | None:
    try:
        raw = json.loads(_disk_path(key).read_text())
        return float(raw["ts"]), raw["value"]
    except Exception:
        return None


def _disk_save(key: str, ts: float, value: Any) -> None:
    try:
        _DISK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _disk_path(key).with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": ts, "value": value}))
        tmp.replace(_disk_path(key))   # atomic swap so readers never see half a file
    except Exception:
        pass


def _store(key: str, value: Any) -> None:
    ts = time.time()
    with _LOCK:
        _STORE[key] = (ts, value)
    _disk_save(key, ts, value)


def _refresh(key: str, builder: Callable[[], Any]) -> None:
    """Rebuild in the background; keep the old value on failure."""
    try:
        _store(key, builder())
    except Exception:
        pass
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def _spawn_refresh(key: str, builder: Callable[[], Any]) -> None:
    """Start one background rebuild per key (single-flight)."""
    with _LOCK:
        if key in _INFLIGHT:
            return
        _INFLIGHT.add(key)
    threading.Thread(
        target=_refresh, args=(key, builder), name=f"refresh:{key}", daemon=True
    ).start()


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def get_or_build(key: str, ttl: int, builder: Callable[[], Any]) -> Any:
    """Return a value for ``key``, never blocking once any snapshot exists.

    Fresh -> return it. Stale (memory or disk) -> return it now and refresh in
    the background. Nothing anywhere -> build synchronously (first request only).
    """
    now = time.time()
    with _LOCK:
        hit = _STORE.get(key)

    # Cold process: seed memory from the last on-disk snapshot if we have one.
    if hit is None:
        disk = _disk_load(key)
        if disk is not None:
            with _LOCK:
                _STORE.setdefault(key, disk)
            hit = disk

    if hit is not None:
        if (now - hit[0]) >= ttl:
            _spawn_refresh(key, builder)   # stale -> serve now, rebuild behind it
        return hit[1]

    # Nothing cached at all: pay the full build once, then everyone rides cache.
    value = builder()
    _store(key, value)
    return value


def invalidate(key: str | None = None) -> None:
    with _LOCK:
        if key is None:
            _STORE.clear()
        else:
            _STORE.pop(key, None)
    try:
        if key is None:
            for p in _DISK_DIR.glob("*.json"):
                p.unlink()
        else:
            _disk_path(key).unlink()
    except Exception:
        pass
