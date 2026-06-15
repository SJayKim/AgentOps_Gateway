"""토큰 버킷 rate limiter (단위) — S5 stretch P5.

시계(_now)를 주입해 실제 시간을 기다리지 않고 버킷 고갈·회복을 결정론적으로 검증한다.
"""

from gateway.ratelimit import RateLimiter


class FakeClock:
    """수동으로 진행시키는 단조 시계."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_burst_up_to_capacity_then_denied():
    clock = FakeClock()
    rl = RateLimiter(capacity=3, refill_per_sec=1.0, now=clock)
    assert [rl.allow("a") for _ in range(3)] == [True, True, True]  # capacity까지 버스트
    assert rl.allow("a") is False  # 4번째는 토큰 고갈 → 거부


def test_refill_restores_one_token_per_interval():
    clock = FakeClock()
    rl = RateLimiter(capacity=1, refill_per_sec=2.0, now=clock)
    assert rl.allow("a") is True
    assert rl.allow("a") is False  # 즉시 재호출 → 거부
    clock.t = 0.5  # 0.5초 × 2 tokens/s = 1 토큰 회복
    assert rl.allow("a") is True


def test_buckets_are_per_agent():
    clock = FakeClock()
    rl = RateLimiter(capacity=1, refill_per_sec=1.0, now=clock)
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    assert rl.allow("b") is True  # 다른 agent는 독립 버킷


def test_refill_capped_at_capacity():
    clock = FakeClock()
    rl = RateLimiter(capacity=2, refill_per_sec=1.0, now=clock)
    assert rl.allow("a") is True  # 2 → 1
    clock.t = 100.0  # 한참 뒤여도 capacity(2)를 넘겨 쌓이지 않는다
    assert [rl.allow("a") for _ in range(2)] == [True, True]
    assert rl.allow("a") is False


def test_from_env_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_RATE_LIMIT", raising=False)
    assert RateLimiter.from_env() is None


def test_from_env_reads_capacity_and_refill(monkeypatch):
    monkeypatch.setenv("GATEWAY_RATE_LIMIT", "5")
    monkeypatch.setenv("GATEWAY_RATE_REFILL", "0.5")
    rl = RateLimiter.from_env()
    assert rl.capacity == 5
    assert rl.refill_per_sec == 0.5
