from xpoll.ratelimit import RateLimiter


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_allows_capacity_then_blocks_with_retry_after():
    tick = Tick()
    limiter = RateLimiter(3, 30, clock=tick)
    assert [limiter.acquire("k") for _ in range(3)] == [None, None, None]
    assert limiter.acquire("k") == 10


def test_refills_over_time():
    tick = Tick()
    limiter = RateLimiter(2, 20, clock=tick)
    limiter.acquire("k")
    limiter.acquire("k")
    assert limiter.acquire("k") is not None
    tick.t = 10
    assert limiter.acquire("k") is None
    tick.t = 1000
    assert [limiter.acquire("k") for _ in range(2)] == [None, None]
    assert limiter.acquire("k") is not None


def test_keys_are_independent():
    limiter = RateLimiter(1, 60, clock=Tick())
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None
    assert limiter.acquire("b") is None


def test_refund_restores_token_up_to_capacity():
    limiter = RateLimiter(1, 60, clock=Tick())
    limiter.acquire("k")
    limiter.refund("k")
    assert limiter.acquire("k") is None
    limiter.refund("k")
    limiter.refund("k")
    assert limiter.acquire("k") is None
    assert limiter.acquire("k") is not None


def test_bounded_number_of_keys():
    limiter = RateLimiter(1, 60, max_keys=2, clock=Tick())
    for key in ("a", "b", "c"):
        limiter.acquire(key)
    assert len(limiter._buckets) == 2
    assert limiter.acquire("a") is None  # evicted, so it starts fresh
