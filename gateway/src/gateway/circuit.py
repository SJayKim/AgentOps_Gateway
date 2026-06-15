"""백엔드별 회로 차단기(circuit breaker) — S5 stretch(P5) 2순위.

[한 줄로 뭘 하나 — 비유]
집의 두꺼비집(누전차단기)과 똑같다. 어떤 백엔드가 계속 말썽이면 그쪽으로 가는 스위치를
잠깐 '탁' 내려 둔다. 그동안은 호출을 아예 안 보내고 즉시 실패로 돌려준다. 일정 시간이 지나면
스위치를 딱 한 번만 살짝 올려 시험해 보고 — 멀쩡하면 정상 복귀, 아직도 말썽이면 도로 내린다.

[왜 회로 차단기인가]
design.md "우선순위 순서"에서 rate limiting 다음 stretch로 못 박았다. 한 백엔드가 죽으면
그쪽 tool 호출은 매번 재연결 1회 시도(upstream.call) 후에야 BACKEND_UNAVAILABLE을 돌려준다
— 죽은 백엔드에 계속 요청이 쌓이면 latency가 악화되고 장애가 전파된다. 회로 차단기는
연속 실패가 threshold에 닿으면 open으로 전환해 그 백엔드 호출을 '시도조차 않고' 즉시
실패시키고(fail-fast), tools/list에서도 빼서 에이전트가 죽은 tool을 보지 못하게 한다.

[상태 모델 — 두꺼비집 스위치의 세 가지 위치]
  - closed   (스위치 올라감 / 정상)   : 호출을 그대로 통과시킨다. 연속 실패가 threshold(정해 둔
                                        기준 횟수)에 닿으면 open으로 넘어간다.
  - open      (스위치 내림 / 차단)     : cooldown_s(식히는 시간) 동안 아예 호출을 안 보내고 즉시
                                        거부한다. tools/list 목록에서도 이 백엔드 tool을 숨긴다.
  - half-open (살짝 올려 떠보기)        : cooldown이 지나면 딱 1번만 시험 호출(probe)을 허용한다.
                                        성공하면 closed로 복구, 실패하면 도로 open(타이머 리셋).

[기본 비활성 — opt-in stretch]
ratelimit.py와 같은 이유로 GATEWAY_CIRCUIT_THRESHOLD 미설정이면 from_env()가 None을 돌려
주고, 그러면 route_call·aggregate가 회로 차단 단계를 통째로 건너뛴다(기존 동작 무영향).

[시계 주입]
open 경과 판정은 시간에 의존한다. _now 콜러블을 주입 가능하게 둬서 단위 테스트가 실제
시간을 기다리지 않고 결정론적으로 open→half-open 전환을 검증한다(ratelimit.py와 동일).
"""

import os
import time


class CircuitBreaker:
    """백엔드(server)별 회로 차단기. threshold회 연속 실패 → open, cooldown_s 후 half-open."""

    def __init__(self, threshold: int, cooldown_s: float, now=time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._now = now
        self._failures: dict[str, int] = {}  # server → 연속 실패 수
        self._opened_at: dict[str, float] = {}  # server → open된 시각 (키 없으면 closed)
        self._probing: set[str] = set()  # half-open probe가 진행 중인 server

    @classmethod
    def from_env(cls) -> "CircuitBreaker | None":
        """GATEWAY_CIRCUIT_THRESHOLD가 있으면 CircuitBreaker, 없으면 None(비활성)."""
        threshold = os.environ.get("GATEWAY_CIRCUIT_THRESHOLD")
        if not threshold:
            return None
        # cooldown 미지정이면 design.md 명시값 30초를 기본으로.
        cooldown = float(os.environ.get("GATEWAY_CIRCUIT_COOLDOWN", 30.0))
        return cls(int(threshold), cooldown)

    def allow(self, server: str) -> bool:
        """이 백엔드로 호출을 보내도 되나. open이고 cooldown 전이면 False(fail-fast)."""
        opened = self._opened_at.get(server)
        if opened is None:
            return True  # closed — 정상 통과
        if self._now() - opened < self.cooldown_s:
            return False  # open, 아직 식는 중 — 시도조차 안 함
        if server in self._probing:
            return False  # half-open: probe가 이미 떠 있음 — 1회만 허용하므로 거부
        self._probing.add(server)  # cooldown 경과 → half-open으로 probe 1회 허용
        return True

    def record_success(self, server: str) -> None:
        """호출 성공 → 회로를 닫는다(실패 카운트·open·probe 상태 전부 청소)."""
        self._failures.pop(server, None)
        self._opened_at.pop(server, None)
        self._probing.discard(server)

    def record_failure(self, server: str) -> None:
        """호출 실패 → 카운트 증가. threshold에 닿으면 open(타이머 리셋)."""
        self._probing.discard(server)  # half-open probe였다면 끝났다
        self._failures[server] = self._failures.get(server, 0) + 1
        if self._failures[server] >= self.threshold:
            self._opened_at[server] = self._now()  # (재)open — half-open probe 실패도 여기로 온다

    def is_tripped(self, server: str) -> bool:
        """tools/list 집계에서 제외할지 — open(half-open probe 중 포함) 상태면 True."""
        return server in self._opened_at
