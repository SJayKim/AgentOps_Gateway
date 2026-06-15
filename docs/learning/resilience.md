# 학습 자료: `장애·과부하 방어 (Rate Limiting + Circuit Breaker)` 완전 해부

> 대상: `gateway/src/gateway/ratelimit.py`, `gateway/src/gateway/circuit.py` (+ 끼워 넣은 곳: `routes.py`, `aggregate.py`, `app.py`)
> 목적: 게이트웨이가 "쏟아지는 호출"과 "죽은 백엔드" 두 가지 위험으로부터 스스로를 지키는 두 장치를, 코드를 모르는 사람도 비유부터 줄별까지 따라올 수 있게 해부한다.
> 관련 스펙: `docs/specs/05-observability.md`(S5 stretch), `docs/design/agentops-gateway-design.md` "우선순위 순서"(P5)

---

## 0. 큰 그림

```
                    [ AI 에이전트 (클라이언트) ]
                              │  tools/call
                              ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                     GATEWAY  (:8000)                           │
   │                                                                │
   │   routes.route_call 안의 검문 순서:                            │
   │                                                                │
   │   ① 토큰 통 비었나?  ──► 비었으면  RATE_LIMITED  (과부하 방어)   │
   │      (rate limiter)        ↑ tool 해석·정책보다 먼저            │
   │   ② tool 해석 → 정책 평가  (auth.md / policy.md 참조)           │
   │   ③ 이 백엔드 회로 내려갔나? ─► 내려갔으면 BACKEND_UNAVAILABLE   │
   │      (circuit breaker)     ↑ 정책 통과 후, 백엔드 호출 직전     │
   │   ④ 백엔드 호출 → 결과로 회로에 성공/실패 보고                  │
   └───────┬───────────────────┬───────────────────┬───────────────┘
           │ ticket__          │ docs__            │ ops__
           ▼                   ▼                   ▼
      [:8101 ticket]      [:8102 docs]       [:8103 ops]
           ▲
           └─ 회로가 내려간 백엔드의 tool은 tools/list 목록에서도 숨겨진다(aggregate.py)
```

**비유로 먼저.** 이 두 장치는 게이트웨이의 **안전장치 두 개**다.

- **Rate limiting(속도 제한)** 은 **놀이공원 자유이용권**이다. 클라이언트마다 토큰이 든 통을 하나씩 쥐여 주고, 도구를 한 번 부를 때마다 토큰 한 장을 쓰게 한다. 통이 비면 그 손님은 잠깐 줄 밖으로 보낸다. 그리고 시간이 지나면 통을 초당 정해진 장수만큼 다시 채워 준다 — 평소 속도는 지키되 잠깐의 몰림은 봐주는 방식이다.
- **Circuit breaker(회로 차단기)** 는 **집의 두꺼비집(누전차단기)** 이다. 어떤 백엔드가 계속 말썽이면 그쪽 스위치를 잠깐 '탁' 내려 둔다. 그동안은 호출을 아예 안 보내고 즉시 실패로 돌려준다. 일정 시간 뒤 스위치를 딱 한 번 살짝 올려 시험해 보고 — 멀쩡하면 정상 복귀, 아직도 말썽이면 도로 내린다.

**왜 둘 다 필요한가.** 게이트웨이는 모든 호출의 단일 통로다. 통로 하나가 막히거나 느려지면 전체가 같이 무너진다. 두 위험이 있다. 첫째, **한 클라이언트가 호출을 쏟아부으면**(버그든 악의든) 다른 정상 클라이언트까지 느려진다 → rate limiting이 입구에서 막는다. 둘째, **한 백엔드가 죽으면** 그쪽 호출이 매번 '재연결 시도 후 실패'를 거치며 느려지고, 그 지연이 게이트웨이 전체로 번진다(장애 전파) → circuit breaker가 죽은 백엔드를 빠르게 끊어 낸다.

**왜 stretch(opt-in)인가.** 둘 다 `design.md`의 우선순위에서 **P5(가장 마지막 stretch)** 다. 핵심 가치(권한 매트릭스 강제·관측성)가 아니라 "여유가 되면 더하는 방어막"이라는 뜻이다. 그래서 **기본은 꺼져 있다**: 해당 환경변수를 주지 않으면 두 장치 모두 `None`이 되어, 요청 경로가 그 단계를 통째로 건너뛴다. 즉 **켜기 전에는 기존 동작·테스트·데모에 전혀 영향이 없다.**

| 파일 | 역할 (한 줄) | 비유 |
| --- | --- | --- |
| `ratelimit.py` | 클라이언트별 토큰 버킷 — 단위 시간당 호출 횟수 제한 | 자유이용권 통 (초당 N장 충전) |
| `circuit.py` | 백엔드별 회로 차단기 — 죽은 백엔드를 fail-fast로 끊기 | 두꺼비집 (말썽이면 스위치 내림) |
| `routes.py`(끼운 곳) | 단계 ①에서 rate limit, 단계 ③에서 circuit을 호출 | 검문소의 두 관문 |
| `aggregate.py`(끼운 곳) | 회로 내려간 백엔드 tool을 `tools/list`에서 제외 | 고장 난 놀이기구는 안내판에서 가림 |
| `app.py`(끼운 곳) | `from_env()`로 두 장치를 만들고 `route_call`에 넘김 | 안전장치 설치·배선 |

---

## 1. `ratelimit.py` — 클라이언트별 토큰 버킷

### 배경: 왜 토큰 버킷인가

`design.md` "우선순위 순서"가 rate limiting을 **"클라이언트별 token bucket"** 으로 못 박았다. 토큰 버킷이라는 고전 알고리즘을 고른 이유는 두 마리 토끼를 잡기 때문이다. **평균 속도**(초당 채워지는 양 = `refill`)는 강제하되, **짧은 버스트**(통 크기 = `capacity`)는 허용한다. 에이전트가 작업 하나를 처리하느라 한순간에 몇 건을 몰아 부르는 건 정상 패턴이므로 통과시키고, **쉬지 않고 계속 쏟아붓는 홍수만** 막는다. "초당 딱 N번"처럼 칼같이 끊는 단순 카운터보다 현실의 트래픽에 너그럽고 안전하다.

### 줄별 해설

```python
class RateLimiter:
    def __init__(self, capacity: int, refill_per_sec: float, now=time.monotonic):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._now = now
        self._buckets: dict[str, tuple[float, float]] = {}  # agent → (tokens, last_seen)
```

- **무엇.** 토큰 통의 규격을 정한다. `capacity`는 통 크기(=한 번에 허용하는 최대 버스트), `refill_per_sec`는 초당 다시 채우는 토큰 수(=평균 허용 속도). `_buckets`는 **에이전트마다** `(현재 토큰 수, 마지막으로 본 시각)`을 따로 들고 있는 장부다.
- **왜 `now`를 주입받나.** 토큰 회복은 "시간이 얼마나 흘렀나"에 달려 있다. 시계 함수(`_now`)를 밖에서 바꿔 끼울 수 있게 둔 건 **단위 테스트가 진짜로 1초를 기다리지 않아도** 되게 하기 위해서다. 테스트는 가짜 시계를 넣어 "0.5초 흐른 척"하며 통이 비고 차는 걸 결정론적으로 검증한다.

```python
    @classmethod
    def from_env(cls) -> "RateLimiter | None":
        cap = os.environ.get("GATEWAY_RATE_LIMIT")
        if not cap:
            return None
        capacity = int(cap)
        refill = float(os.environ.get("GATEWAY_RATE_REFILL", capacity))
        return cls(capacity, refill)
```

- **무엇.** 환경변수에서 설정을 읽어 객체를 만든다. `GATEWAY_RATE_LIMIT`(통 크기)이 **없으면 `None`을 돌려준다** — 이게 "이 기능 끔"의 신호다. `GATEWAY_RATE_REFILL`(초당 회복량)을 안 주면 기본값을 `capacity`로 둔다(= "1초면 빈 통이 가득 찬다").
- **왜 `None`이 핵심인가.** 이 `None` 한 줄이 **opt-in stretch의 스위치**다. `app.py`가 이 `None`을 받아 그대로 `route_call`에 넘기면, `route_call`은 rate-limit 단계를 건너뛴다. 즉 **환경변수를 안 주면 기능 자체가 경로에서 사라진다** — 기존 동작 보존의 가장 단순한 구현이다.

```python
    def allow(self, agent: str) -> bool:
        now = self._now()
        tokens, last = self._buckets.get(agent, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens < 1:
            self._buckets[agent] = (tokens, now)  # 시각만 갱신
            return False
        self._buckets[agent] = (tokens - 1, now)
        return True
```

- **무엇.** 한 호출을 허용할지 말지 판정하는 심장이다. ① 이 에이전트의 통을 꺼낸다(처음 보는 에이전트면 **가득 찬 통**으로 시작). ② **지난번 본 뒤 흐른 시간만큼 토큰을 다시 채운다** — 흐른 초(`now - last`) × 초당 회복량. ③ 채운 결과가 1장도 안 되면 거부(`False`), 1장 이상이면 한 장 쓰고 허용(`True`).
- **왜 `min(self.capacity, …)`인가.** 통은 **넘치지 않는다**. 오래 안 쓴 에이전트라고 토큰이 무한정 쌓이면, 한참 쉬었다가 갑자기 수천 번 호출하는 폭주를 막지 못한다. `min`으로 가득 찬 지점(`capacity`)에서 멈춰, 버스트 허용량을 통 크기로 묶어 둔다.
- **왜 거부할 때도 시각(`last`)을 갱신하나.** 다음번 회복량을 "직전에 본 시각부터"가 아니라 "지금부터" 다시 재기 위해서다. 시각을 안 고치면 거부가 반복될 때마다 흐른 시간이 중복으로 계산돼 회복이 과대평가된다. 토큰은 그대로 두고 **시각만** 갱신하는 게 정확하다.

(메타포: `allow`는 **자유이용권 개찰구**다. 손님이 올 때마다 "그동안 통에 몇 장이 다시 찼지?"를 먼저 계산해 넣고(단, 통 크기를 넘기진 않게), 한 장이라도 있으면 찍고 들여보낸다. 통이 비었으면 "잠시 후 다시 오세요" — 그게 `RATE_LIMITED`다.)

---

## 2. `circuit.py` — 백엔드별 회로 차단기

### 배경: 왜 회로 차단기인가

`design.md`가 rate limiting **다음** stretch로 못 박은 장치다. 문제 상황은 이렇다: 한 백엔드가 죽으면, 그쪽 tool 호출은 매번 `upstream`이 **재연결을 1회 시도**한 뒤에야 `BACKEND_UNAVAILABLE`을 돌려준다(`gateway-core.md` 3장 참조). 죽은 백엔드에 요청이 계속 쌓이면 그 "시도 후 실패"의 지연이 누적돼 latency가 나빠지고 장애가 번진다. 회로 차단기는 **연속 실패가 기준(threshold)에 닿으면 그 백엔드를 `open`(차단)으로 바꿔**, 이후 호출은 시도조차 않고 즉시 실패시킨다(fail-fast). 게다가 `tools/list`에서도 빼서, 에이전트가 죽은 tool을 아예 보지 못하게 한다.

### 상태 기계 — 스위치의 세 위치

회로는 백엔드마다 세 상태 중 하나에 있다(자세한 그림은 모듈 docstring):

- **closed(정상)** — 호출 통과. 연속 실패가 `threshold`에 닿으면 → `open`.
- **open(차단)** — `cooldown_s` 동안 호출을 안 보내고 즉시 거부. `tools/list`에서도 제외.
- **half-open(떠보기)** — `cooldown` 경과 후 **딱 1번** 시험 호출(probe) 허용. 성공 → `closed`로 복구, 실패 → 다시 `open`(타이머 리셋).

### 줄별 해설

```python
    def __init__(self, threshold: int, cooldown_s: float, now=time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._now = now
        self._failures: dict[str, int] = {}      # server → 연속 실패 수
        self._opened_at: dict[str, float] = {}   # server → open된 시각 (키 없으면 closed)
        self._probing: set[str] = set()          # half-open probe 진행 중인 server
```

- **무엇.** 차단기의 규격과 상태 장부다. `threshold`=몇 번 연속 실패하면 내릴지, `cooldown_s`=내린 뒤 식히는 시간. 상태는 세 장부로 표현된다: 연속 실패 횟수(`_failures`), 언제 내려갔는지(`_opened_at` — **키가 있으면 곧 차단 상태**), 시험 호출이 떠 있는지(`_probing`).
- **왜 `_opened_at`에 키가 있냐 없냐로 상태를 표현하나.** 별도의 "상태 enum 필드"를 두지 않고, **"open된 시각이 기록돼 있으면 차단 중, 없으면 정상"** 으로 단순화했다. 시각 하나로 "차단 여부 + 언제부터(=cooldown 계산용)"를 동시에 표현하니 군더더기가 없다.

```python
    @classmethod
    def from_env(cls) -> "CircuitBreaker | None":
        threshold = os.environ.get("GATEWAY_CIRCUIT_THRESHOLD")
        if not threshold:
            return None
        cooldown = float(os.environ.get("GATEWAY_CIRCUIT_COOLDOWN", 30.0))
        return cls(int(threshold), cooldown)
```

- **무엇/왜.** rate limiter와 **똑같은 opt-in 패턴**이다. `GATEWAY_CIRCUIT_THRESHOLD`가 없으면 `None`(비활성). `cooldown`을 안 주면 `design.md`가 명시한 **30초**를 기본값으로 쓴다. `None`을 받은 `route_call`·`aggregate`는 회로 단계를 통째로 건너뛴다(기존 동작 무영향).

```python
    def allow(self, server: str) -> bool:
        opened = self._opened_at.get(server)
        if opened is None:
            return True                              # closed — 정상 통과
        if self._now() - opened < self.cooldown_s:
            return False                             # open, 아직 식는 중 — 시도조차 안 함
        if server in self._probing:
            return False                             # half-open: probe가 이미 떠 있음
        self._probing.add(server)                    # cooldown 경과 → probe 1회 허용
        return True
```

- **무엇.** "이 백엔드로 호출을 보내도 되나"를 판정한다. ① open된 적 없으면(`opened is None`) **정상**이니 통과. ② open됐고 아직 cooldown이 안 지났으면 **차단**(시도조차 안 함). ③ cooldown은 지났는데 이미 시험 호출이 떠 있으면 거부(1번만 허용하므로). ④ cooldown이 지났고 시험 호출이 없으면 — **딱 한 번** probe를 허용하고 `_probing`에 등록.
- **왜 half-open에 probe를 1개만 허용하나.** cooldown이 끝났다고 갑자기 호출을 다 풀어 주면, 백엔드가 아직 못 일어났을 때 또 한꺼번에 몰려 무너진다. **대표로 한 건만** 보내 살아났는지 떠보고, 그 결과로 완전 복구할지 다시 차단할지 정한다. 신중한 재진입이다.

```python
    def record_success(self, server: str) -> None:
        self._failures.pop(server, None)
        self._opened_at.pop(server, None)
        self._probing.discard(server)
```

- **무엇/왜.** 호출이 성공하면 그 백엔드의 **모든 실패 흔적을 깨끗이 지운다**(실패 카운트·open 시각·probe 표시). 한 번 잘 됐으면 회로를 `closed`로 완전히 되돌리는 것이다. half-open 시험 호출이 성공한 경우도 여기로 와서 정상 복구된다.

```python
    def record_failure(self, server: str) -> None:
        self._probing.discard(server)
        self._failures[server] = self._failures.get(server, 0) + 1
        if self._failures[server] >= self.threshold:
            self._opened_at[server] = self._now()    # (재)open — 타이머 리셋
```

- **무엇.** 호출이 실패하면 연속 실패 카운트를 1 올리고, 그 수가 `threshold`에 닿으면 **지금 시각으로 open**시킨다(=스위치 내림 + cooldown 타이머 시작).
- **왜 맨 앞에서 `_probing`을 비우나.** 이 실패가 half-open 시험 호출의 결과일 수 있다. probe가 끝났음을 먼저 표시하고, 실패 카운트가 threshold를 넘으면 그대로 다시 open된다 — **"시험 호출 실패 → 도로 차단 + 타이머 리셋"** 이 자연히 이뤄진다.

```python
    def is_tripped(self, server: str) -> bool:
        return server in self._opened_at
```

- **무엇/왜.** "이 백엔드를 `tools/list`에서 빼야 하나"를 묻는 질문이다. `_opened_at`에 키가 있으면(=차단 중, half-open으로 떠보는 중 포함) `True`. `aggregate.py`가 이걸 보고 죽은 백엔드 tool을 목록에서 숨긴다.

(메타포: 차단기는 **계속 넘어지는 직원을 잠깐 쉬게 하는 관리자**다. 세 번 연속 실수하면(`threshold`) "오늘은 좀 쉬어"(open)라고 일을 안 맡긴다. 30초 뒤 "자, 한 건만 해볼래?"(half-open probe)라고 시켜 보고, 잘하면 정상 복귀, 또 실패하면 다시 쉬게 한다. 그동안 손님에게는 "그 창구는 지금 닫혔어요"라고 빨리 안내한다 — 무작정 기다리게 하지 않는다.)

---

## 3. Live wiring: 두 장치가 실제 요청 흐름에 끼는 곳

두 모듈은 **독립 파일**이고, 직접 요청을 받지 않는다. `routes.py`의 `route_call`이 정해진 자리에서 불러 줘야 동작한다. **(중요: circuit breaker는 `upstream.py` 안이 아니라 `routes.py`에서 호출된다.)**

`app.py`가 기동 시 `from_env()`로 두 장치를 만들고(없으면 `None`), 모든 `tools/call`마다 `route_call(..., rate_limiter, breaker)`로 넘긴다. 그러면 `route_call` 안에서(괄호 안은 `routes.py` 주석의 실제 단계 번호 0~6):

1. **rate limit (단계 0)** — tool 이름을 풀거나 정책을 따지기 **전에** `rate_limiter.allow(agent)`를 본다. 통이 비었으면 곧장 `RATE_LIMITED` 결과 + `"rate_limited"` decision. 맨 앞인 이유: 호출을 쏟아붓는 클라이언트는 어느 tool을 두드리든 **입구에서** 막아 뒷단 작업을 낭비시키지 않기 위해서다.
2. **tool 해석 + 정책 평가 (단계 1~4)** — `gateway-core.md` 4장 / `policy.md`가 다루는 기존 관문(여기선 변화 없음).
3. **circuit allow (단계 5)** — 정책까지 통과한 '보낼 자격 있는' 호출에 대해서만 `breaker.allow(server)`를 본다. 회로가 내려갔으면(`open`) 백엔드를 부르지 않고 즉시 `BACKEND_UNAVAILABLE`. 정책 **뒤**에 두는 이유: 어차피 거부될 호출로 회로 통계를 더럽히지 않기 위해서다.
4. **결과 보고 (단계 6 직후)** — 백엔드 호출이 끝나면 결과를 회로에 보고한다. `BACKEND_UNAVAILABLE`(인프라 실패)만 `record_failure`, 그 외(tool 자체 오류 포함 = 백엔드는 살아 있음)는 `record_success`.

그리고 **`tools/list` 경로**(`aggregate.aggregate_tools(backends, breaker)`)에서는 `breaker.is_tripped(backend.name)`가 `True`인 백엔드의 tool을 목록에서 **제외**한다 — 에이전트가 죽은 tool을 보지도 부르지도 않게.

> 결과로 새로 생기는 신호: `RATE_LIMITED`·`BACKEND_UNAVAILABLE` 오류 코드(둘 다 `errors.error_result` 같은 봉투)와, 메트릭·audit의 `decision="rate_limited"` 라벨. **새 Prometheus 카운터나 Grafana 패널을 만들지는 않는다** — 기존 `TOOL_CALLS` 카운터에 라벨 값 하나가 늘었을 뿐이다(`observability.md` 참조).

---

## 4. 관통하는 설계 원칙 요약

- **기본은 꺼짐(opt-in stretch)**: 두 장치 모두 환경변수가 없으면 `from_env()`가 `None`을 돌려주고, 요청 경로가 그 단계를 통째로 건너뛴다. 켜기 전엔 기존 동작·테스트·데모에 무영향 — P5 stretch를 안전하게 더하는 방식.
- **두 위험, 두 장치**: 과부하(쏟아지는 호출)는 **클라이언트별** 토큰 버킷으로 입구에서, 장애(죽은 백엔드)는 **백엔드별** 회로 차단기로 출구에서 막는다. 막는 단위(클라이언트 vs 백엔드)와 자리(정책 전 vs 정책 후)가 다르다.
- **버스트는 봐주되 홍수는 막는다**: 토큰 버킷은 `capacity`만큼의 순간 몰림은 통과시키고 `refill` 평균 속도만 강제한다. `min(capacity, …)`으로 토큰이 무한정 쌓이지 않게 해 "오래 쉰 뒤 폭주"도 막는다.
- **죽은 백엔드는 빠르게 끊는다(fail-fast)**: 회로가 `open`이면 재연결 시도조차 건너뛰고 즉시 실패시켜, 한 백엔드 장애의 지연이 게이트웨이 전체로 번지지 않게 한다. half-open은 **probe 1건**으로 신중히 재진입한다.
- **회로는 '인프라 실패'만 센다**: tool 자체 오류(없는 ticket_id 등)는 백엔드가 살아 있다는 뜻이라 success로 친다. `BACKEND_UNAVAILABLE`만 failure로 세어, 차단이 "진짜 죽음"에만 반응하게 했다.
- **시계 주입으로 결정론적 테스트**: 두 장치 다 `now` 콜러블을 주입받아, 단위 테스트가 실제 시간을 기다리지 않고 토큰 고갈/회복·`open→half-open` 전환을 검증한다.
- **새 메트릭을 발명하지 않는다**: 가시 효과는 기존 오류 봉투(`errors.error_result`)와 기존 카운터의 새 라벨(`rate_limited`)로만 드러난다 — 관측 스택을 늘리지 않고 기존 어휘에 얹었다.
```
