import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class RateLimiter:
    """In-memory token bucket per key. State resets on restart (single-process by design).

    `check` only peeks, so callers decide which outcomes cost a token via `hit`.
    """

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

    def _tokens(self, key: str, now: float) -> float:
        tokens, updated = self._buckets.get(key, (self.capacity, now))
        return min(self.capacity, tokens + (now - updated) * self.rate)

    def check(self, key: str) -> int | None:
        """None if the key may proceed, else seconds until it may."""
        with self._lock:
            tokens = self._tokens(key, self.clock())
            if tokens >= 1:
                return None
            return max(1, math.ceil((1 - tokens) / self.rate))

    def hit(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._buckets[key] = (max(0.0, self._tokens(key, now) - 1), now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
