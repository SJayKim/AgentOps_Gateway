"""클라이언트별 rate limiting — S5 stretch(P5) 1순위.

[왜 토큰 버킷인가]
design.md "우선순위 순서"에서 rate limiting은 "클라이언트별 token bucket"으로 못 박았다.
토큰 버킷은 평균 속도(refill)를 강제하면서도 짧은 버스트(capacity)를 허용한다 — 에이전트가
한 번에 몇 건 몰아 부르는 정상 패턴은 통과시키되, 지속적인 홍수만 막는다.

[기본 비활성 — opt-in stretch]
stretch 기능이라 GATEWAY_RATE_LIMIT 미설정이면 from_env()가 None을 돌려준다. 그러면
route_call이 레이트리밋 단계를 통째로 건너뛰어 기존 동작·테스트·e2e가 영향을 받지 않는다.

[시계 주입]
allow() 판정은 경과 시간에 의존한다. _now 콜러블을 주입 가능하게 둬서 단위 테스트가
실제 시간을 기다리지 않고 결정론적으로 버킷 고갈/회복을 검증한다.
"""

import os
import time


class RateLimiter:
    """agent별 토큰 버킷. capacity 토큰까지 버스트 허용, 초당 refill_per_sec로 회복."""

    def __init__(self, capacity: int, refill_per_sec: float, now=time.monotonic):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._now = now
        self._buckets: dict[str, tuple[float, float]] = {}  # agent → (tokens, last_seen)

    @classmethod
    def from_env(cls) -> "RateLimiter | None":
        """GATEWAY_RATE_LIMIT(capacity)이 있으면 RateLimiter, 없으면 None(비활성)."""
        cap = os.environ.get("GATEWAY_RATE_LIMIT")
        if not cap:
            return None
        capacity = int(cap)
        # refill 미지정이면 "초당 capacity만큼 회복"(1초면 버킷이 가득 참)을 기본값으로.
        refill = float(os.environ.get("GATEWAY_RATE_REFILL", capacity))
        return cls(capacity, refill)

    def allow(self, agent: str) -> bool:
        """토큰 1개를 소비한다. 잔량이 부족하면 False(=거부)."""
        now = self._now()
        tokens, last = self._buckets.get(agent, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens < 1:
            self._buckets[agent] = (tokens, now)  # 시각만 갱신(다음 회복 계산 기준)
            return False
        self._buckets[agent] = (tokens - 1, now)
        return True
