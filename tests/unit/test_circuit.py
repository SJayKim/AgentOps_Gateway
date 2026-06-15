"""백엔드별 circuit breaker (단위) — S5 stretch P5.

시계(_now)를 주입해 실제 시간을 기다리지 않고 open→half-open 전환을 결정론적으로 검증한다.
"""

from gateway.circuit import CircuitBreaker


class FakeClock:
    """수동으로 진행시키는 단조 시계."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_closed_allows_until_threshold_then_opens():
    cb = CircuitBreaker(threshold=3, cooldown_s=30.0, now=FakeClock())
    assert cb.allow("ops") is True
    cb.record_failure("ops")
    cb.record_failure("ops")
    assert cb.allow("ops") is True  # 아직 2회 — closed
    cb.record_failure("ops")  # 3회째 → open
    assert cb.is_tripped("ops") is True
    assert cb.allow("ops") is False  # open, cooldown 전 → fail-fast


def test_open_fails_fast_within_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0, now=clock)
    cb.record_failure("ops")  # 즉시 open
    clock.t = 29.9
    assert cb.allow("ops") is False  # 아직 식는 중


def test_half_open_allows_single_probe():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0, now=clock)
    cb.record_failure("ops")
    clock.t = 30.0  # cooldown 경과 → half-open
    assert cb.allow("ops") is True  # probe 1회 허용
    assert cb.allow("ops") is False  # probe 진행 중 → 추가 호출 차단


def test_probe_success_closes_circuit():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0, now=clock)
    cb.record_failure("ops")
    clock.t = 30.0
    assert cb.allow("ops") is True  # half-open probe
    cb.record_success("ops")  # probe 성공 → close
    assert cb.is_tripped("ops") is False
    assert cb.allow("ops") is True


def test_probe_failure_reopens_with_reset_timer():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0, now=clock)
    cb.record_failure("ops")
    clock.t = 30.0
    assert cb.allow("ops") is True  # half-open probe
    cb.record_failure("ops")  # probe 실패 → 다시 open, 타이머 리셋(now=30)
    assert cb.is_tripped("ops") is True
    assert cb.allow("ops") is False  # 30 - 30 = 0 < cooldown → 다시 식는 중
    clock.t = 60.0
    assert cb.allow("ops") is True  # 새 cooldown 경과 → 다시 probe


def test_success_resets_failure_count():
    cb = CircuitBreaker(threshold=2, cooldown_s=30.0, now=FakeClock())
    cb.record_failure("ops")
    cb.record_success("ops")  # 카운트 리셋
    cb.record_failure("ops")
    assert cb.is_tripped("ops") is False  # 연속 2회가 아니므로 open 안 됨


def test_breakers_are_per_backend():
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0, now=FakeClock())
    cb.record_failure("ops")
    assert cb.is_tripped("ops") is True
    assert cb.is_tripped("ticket") is False  # 다른 백엔드는 독립
    assert cb.allow("ticket") is True


def test_from_env_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_CIRCUIT_THRESHOLD", raising=False)
    assert CircuitBreaker.from_env() is None


def test_from_env_reads_threshold_and_cooldown(monkeypatch):
    monkeypatch.setenv("GATEWAY_CIRCUIT_THRESHOLD", "5")
    monkeypatch.setenv("GATEWAY_CIRCUIT_COOLDOWN", "10")
    cb = CircuitBreaker.from_env()
    assert cb.threshold == 5
    assert cb.cooldown_s == 10.0


def test_from_env_default_cooldown_is_30(monkeypatch):
    monkeypatch.setenv("GATEWAY_CIRCUIT_THRESHOLD", "3")
    monkeypatch.delenv("GATEWAY_CIRCUIT_COOLDOWN", raising=False)
    cb = CircuitBreaker.from_env()
    assert cb.cooldown_s == 30.0
