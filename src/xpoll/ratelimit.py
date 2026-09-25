import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class RateLimiter:
    """In-memory token bucket per key. State resets on restart (single-process by design)."""

    def __init__(
        self,
        capacity: int,
        per_seconds: float,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.capacity = float(capacity)
        self.rate = capacity / per_seconds
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def _refill(self, key: str, now: float) -> float:
        tokens, updated = self._buckets.get(key, (self.capacity, now))
        return min(self.capacity, tokens + (now - updated) * self.rate)

    def acquire(self, key: str) -> int | None:
        """Take one token. Returns None if allowed, else seconds until a token is available."""
        with self._lock:
            now = self.clock()
            tokens = self._refill(key, now)
            if tokens < 1:
                self._store(key, tokens, now)
                return max(1, math.ceil((1 - tokens) / self.rate))
            self._store(key, tokens - 1, now)
            return None

    def refund(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._store(key, min(self.capacity, self._refill(key, now) + 1), now)

    def _store(self, key: str, tokens: float, now: float) -> None:
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
