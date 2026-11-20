"""Short-lived in-process result cache for expensive fleet-wide reads.

Fleet analytics (anomalies over a week of data for every system) cost seconds and change only when collectors write,
every few minutes. Caching them for INTELLIGENCE_CACHE_SECONDS lets many users and dashboard refreshes share one
computation. The cache is per API process and keyed by database and arguments; 0 disables it."""
import threading
import time
from functools import wraps

from .. import config, db

_lock = threading.Lock()
_entries: dict[tuple, tuple[float, object]] = {}
_MAX_ENTRIES = 256


def ttl_cached(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        ttl = config.INTELLIGENCE_CACHE_SECONDS
        if ttl <= 0:
            return fn(*args, **kwargs)
        key = (fn.__module__, fn.__qualname__, db.DATABASE_URL, args, tuple(sorted(kwargs.items())))
        now = time.monotonic()
        with _lock:
            hit = _entries.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = fn(*args, **kwargs)
        with _lock:
            if len(_entries) >= _MAX_ENTRIES:
                for k in sorted(_entries, key=lambda k: _entries[k][0])[: _MAX_ENTRIES // 4]:
                    _entries.pop(k, None)
            _entries[key] = (time.monotonic(), value)
        return value
    return wrapper


def clear() -> None:
    with _lock:
        _entries.clear()
