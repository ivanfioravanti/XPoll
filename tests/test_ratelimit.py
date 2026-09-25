from xpoll.ratelimit import RateLimiter


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_check_does_not_consume():
    limiter = RateLimiter(1, 60, clock=Tick())
    assert [limiter.check("k") for _ in range(5)] == [None] * 5


def test_blocks_after_capacity_hits_with_retry_after():
    limiter = RateLimiter(3, 30, clock=Tick())
    for _ in range(3):
        assert limiter.check("k") is None
        limiter.hit("k")
    assert limiter.check("k") == 10


def test_refills_over_time():
    tick = Tick()
    limiter = RateLimiter(2, 20, clock=tick)
    limiter.hit("k")
    limiter.hit("k")
    assert limiter.check("k") is not None
    tick.t = 10
    assert limiter.check("k") is None
    tick.t = 1000
    limiter.hit("k")
    limiter.hit("k")
    assert limiter.check("k") is not None


def test_hits_never_go_negative():
    tick = Tick()
    limiter = RateLimiter(1, 10, clock=tick)
    for _ in range(50):
        limiter.hit("k")
    tick.t = 10
    assert limiter.check("k") is None


def test_keys_are_independent():
    limiter = RateLimiter(1, 60, clock=Tick())
    limiter.hit("a")
    assert limiter.check("a") is not None
    assert limiter.check("b") is None


def test_bounded_number_of_keys():
    limiter = RateLimiter(1, 60, max_keys=2, clock=Tick())
    for key in ("a", "b", "c"):
        limiter.hit(key)
    assert len(limiter._buckets) == 2
    assert limiter.check("a") is None  # evicted, so it starts fresh
