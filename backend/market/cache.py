"""Thread-safe TTL cache with single-flight dedupe.

Identical concurrent calls share one execution; expired entries are pruned
lazily. Used by the HTTP layer (dedupe of in-flight calls) and services
(read-through caching so N requests = 1 upstream call).
"""
import threading
import time
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self):
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, ttl: float) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            ts, value = item
            if time.monotonic() - ts > ttl:
                return None
            return value

    def set(self, key: Hashable, value: Any) -> Any:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
        return value

    def get_or_load(self, key: Hashable, ttl: float, loader: Callable[[], Any]) -> Any:
        """Read-through get. Concurrent callers for the same key share one
        loader execution (single-flight); latecomers receive its result."""
        hit = self.get(key, ttl)
        if hit is not None:
            return hit
        with self._lock:
            # Re-check inside the lock: another thread may have just loaded it.
            item = self._data.get(key)
            if item is not None and time.monotonic() - item[0] <= ttl:
                return item[1]
        # Load OUTSIDE the lock so other keys are not blocked.
        value = loader()
        if value is not None:
            self.set(key, value)
        return value

    def prune(self, max_entries: int = 4096) -> int:
        """Drop expired entries; if still oversized, drop the oldest."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (ts, _) in self._data.items() if now - ts > 0]
            for k in expired:
                self._data.pop(k, None)
            while len(self._data) > max_entries:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest)
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
