# 학습 자료: `YAML 정책 엔진` 완전 해부

> 대상: gateway/src/gateway/policy.py, policies/policy.yaml
> 목적: 어떤 에이전트가 어떤 tool을 호출할 수 있는지를 데이터(YAML)로 선언하고, 그 규칙을 tool call 단위로 강제하는 "문지기"가 어떻게 동작하는지를 한 줄도 빠짐없이 추적한다.
> 관련 스펙: docs/specs/04-auth-policy-audit.md, docs/design/agentops-gateway-design.md

---

## 0. 큰 그림

```
                  요청: (agent_id, server, tool, args)
                            │
                            ▼
        ┌───────────────────────────────────────────┐
        │  Policy.evaluate()                         │
        │  1) rule = "agent:server:tool" 식별자 생성  │
        │  2) YAML에서 agent→server 허용 목록 조회     │
        │  3) 목록 순회                                │
        │      ├ 문자열 일치  → 허용 (제약 없음)        │
        │      ├ dict 일치    → _check_args() 인자검사  │
        │      └ 아무것도 안 맞음 → 거부 (default-deny) │
        └───────────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   Decision(allowed=True)      Decision(allowed=False, rule, detail?)
        통과 → 백엔드로            → POLICY_DENIED payload로 변환

   권한 매트릭스 (행=에이전트, 열=서버)
   ┌───────────────┬──────────────┬────────────┬──────────────────┐
   │               │ ticket       │ docs       │ ops              │
   ├───────────────┼──────────────┼────────────┼──────────────────┤
   │ support-agent │ 읽기+쓰기     │ 읽기        │ 차단 (미기재)     │
   │ analyst-agent │ 읽기(search) │ 읽기        │ 읽기 (logs≤24h)  │
   │ dev-agent     │ 읽기+쓰기     │ 읽기+쓰기   │ 읽기+쓰기         │
   └───────────────┴──────────────┴────────────┴──────────────────┘
```

이 모듈은 빌딩의 **출입 허가 매트릭스를 든 문지기**다. 빌딩 정문(Gateway) 안에는 세 개의 방(ticket·docs·ops 서버)이 있고, 세 명의 직원(support·analyst·dev 에이전트)이 드나든다. 문지기는 누가 어느 방에서 무엇을 할 수 있는지가 적힌 한 장의 명단(`policy.yaml`)을 손에 들고 있다. 누군가 방문을 두드릴 때마다 문지기는 명단을 펼쳐 "이 사람 — 이 방 — 이 동작"이 적혀 있는지 확인한다. 적혀 있으면 통과, 없으면 거부다.

핵심은 **명단에 없으면 무조건 거부**(default-deny)라는 점이다. 문지기는 "금지된 사람 목록"을 들고 있지 않다. 그 반대로 "허용된 조합 목록"만 들고 있어서, 명단에 빠진 모든 것은 자동으로 막힌다. 새로운 방이나 새로운 동작이 추가돼도 명단에 명시적으로 적기 전까지는 들어갈 수 없다. 이것이 안전한 기본값이다 — 빠뜨리면 사고(허용)가 아니라 차단으로 떨어진다.

그리고 이 문지기는 **절대 패닉(예외)에 빠지지 않는다**. 누가 찢어진 신분증이나 거꾸로 적힌 날짜를 들이밀어도, 문지기는 당황해서 문을 활짝 열어두는 일이 없다. 의심스러우면 항상 거부다(fail-closed). 정책 계층은 시스템이 외부의 공격적·기형적 입력을 가장 먼저 만나는 최전선이기 때문에, 여기서의 견고함이 시스템 전체의 안전을 좌우한다.

| 파일 | 역할 |
|------|------|
| `policies/policy.yaml` | 권한 매트릭스의 데이터 표현. "누가 어디서 무엇을" 허용되는지의 명단. 코드 수정 없이 정책만 바꾼다. |
| `gateway/src/gateway/policy.py` | 그 명단을 읽어 들이고(`load`), 검증하고(`warn_unknown_tools`), 매 요청마다 판정하는(`evaluate`) 엔진. |

---

## 1. `policy.yaml` — 정책 데이터 (코드가 무엇을 해석하는지 먼저)

### 배경

엔진을 이해하기 전에, 엔진이 해석하는 **데이터**부터 봐야 한다. 정책을 코드가 아니라 YAML 파일에 둔 이유는 명확하다: 누가 무엇에 접근할 수 있는지는 **운영 정책**이지 프로그램 로직이 아니다. 권한을 한 줄 바꾸려고 코드를 고치고 재배포·재테스트하는 것은 과하다. 데이터와 코드를 분리하면, 매트릭스 변경은 YAML 한 줄 수정으로 끝난다.

### 줄별 해설 (YAML 구조)

```yaml
support-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
  # ops 미기재 = 전부 거부
```

- **의미**: `support-agent`는 ticket 서버에서 3개 tool(생성·검색·상태변경 = 읽기+쓰기 전부), docs 서버에서 2개 tool(검색·읽기)을 쓸 수 있다.
- **왜?**: ops(운영 로그/메트릭) 항목이 **아예 없다**. 블랙리스트 방식이라면 "ops를 막아라"라고 따로 적어야 하지만, default-deny에서는 **적지 않는 것 자체가 차단**이다. 주석이 그 의도를 못 박아 둔다. 빠뜨림이 곧 안전이다.

```yaml
analyst-agent:
  ticket: [search_tickets]
  docs: [search_docs, read_doc]
  ops:
    - get_metrics
    - tool: query_logs
      max_range_hours: 24
```

- **의미**: `analyst-agent`는 ticket에서 **검색만**(`search_tickets` 하나 — 쓰기 tool인 create/update는 빠짐 = 거부), docs는 읽기, ops는 `get_metrics`와 `query_logs`를 쓸 수 있다.
- **왜? (핵심 시연 포인트)**: ops 목록의 두 항목은 **형태가 다르다**. `get_metrics`는 그냥 문자열(제약 없는 단순 허용)이지만, `query_logs`는 `tool:`/`max_range_hours:`를 가진 **dict**다. 이것이 이 프로젝트 유일한 **인자 레벨 정책**이다: analyst는 로그를 조회하되 한 번에 최대 24시간 범위까지만 볼 수 있다. 같은 YAML이 "tool을 쓸 수 있나?"(접근 제어)와 "어떻게 쓸 수 있나?"(인자 제약) 두 차원을 동시에 표현한다.

```yaml
dev-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
  ops: [get_metrics, query_logs]
```

- **의미**: `dev-agent`는 세 서버 모두에서 모든 tool을 쓴다.
- **왜? (매트릭스 차등 시연)**: dev의 `query_logs`는 문자열이라 **시간 제약이 없다**. 같은 tool인데도 analyst에게는 24h 제약이 붙고 dev에게는 안 붙는다. 이것이 의도된 설계다 — 정책 엔진이 단순 on/off가 아니라 **에이전트별 차등 제약**까지 표현할 수 있음을 보여주는 데모 장치다.

> **데이터 구조 요약**: `agent_id → { server → [ 항목들 ] }`. 각 항목은 **문자열**(제약 없는 허용) 또는 **dict**(`tool` 키 + 제약 필드). 이 두 가지 형태를 코드가 분기 처리한다 — 2절에서 본다.

---

## 2. `policy.py` — 정책 평가 엔진

### 배경: 왜 이렇게 설계했나

이 모듈은 **프로젝트의 존재 이유**다. 모듈 docstring(4~6행)이 design.md 전제 3을 인용한다: *"Gateway가 없으면 이 매트릭스를 강제할 방법이 없다."* 권한 매트릭스를 실제로 강제하는 곳이 바로 여기다. 설계의 세 기둥:

1. **default-deny (화이트리스트)** — docstring 8~11행. 명시 허용만 통과, 나머지 전부 거부. 블랙리스트였다면 새 tool마다 사람이 "막아야 하나?"를 챙겨야 하고 빠뜨리면 사고지만, 화이트리스트는 빠뜨림이 곧 안전한 실패(거부)다.
2. **evaluate는 절대 예외를 던지지 않는다** — docstring 13~16행, eng review T3. 정책 계층은 공격적 입력을 가장 먼저 만나는 곳이라, 여기서 예외가 터지면 처리 경로가 깨지며 **fail-open(우연한 허용)** 위험이 생긴다. 그래서 어떤 입력에도 예외 대신 항상 `Decision`을 반환하고, 의심스러우면 거부(fail-closed)한다.

### 줄별 해설

#### `Decision` — 판정 결과 한 묶음

```python
@dataclass
class Decision:
    allowed: bool
    rule: str  # "<agent>:<server>:<tool>" — POLICY_DENIED payload의 rule 필드 계약(에이전트가 파싱)
    detail: str | None = None  # 인자 레벨 위반 등 추가 설명 (예: 시간 범위 초과 사유)
```

- **의미**: 판정 결과를 담는 작은 데이터 봉투. 허용 여부(`allowed`), 사람이 읽을 수 있는 규칙 식별자(`rule`), 그리고 인자 위반 시에만 채워지는 설명(`detail`).
- **왜?**: `rule`이 `"agent:server:tool"` 형식인 것은 우연이 아니라 **계약**이다. 거부 응답 payload에 그대로 실려 에이전트(클라이언트)가 파싱한다 — 정확한 연결은 3절에서 본다. `detail`은 인자 위반(예: 시간 범위 초과)에서만 추가 사유를 싣는 선택 필드다.

#### `Policy.__init__` / `load` — 명단을 손에 쥔다

```python
class Policy:
    def __init__(self, agents: dict):
        self.agents = agents

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})
```

- **의미**: `load`가 YAML 파일을 읽어 `Policy` 인스턴스를 만든다. 이게 기동 시 **딱 1회** 호출된다 — 핫 리로드는 없다(정책을 바꾸려면 재기동).
- **왜? (`safe_load`)**: `yaml.load`가 아니라 `safe_load`를 쓴 것은 보안 결정이다. `load`는 YAML 안에 적힌 임의 파이썬 객체를 역직렬화할 수 있어 위험하다. 정책 파일은 신뢰 경계 밖에 있을 수 있으므로 `safe_load`로 순수 데이터만 읽는다.
- **왜? (`or {}`)**: 빈 파일이면 `safe_load`가 `None`을 반환한다. `or {}`로 빈 dict로 정규화하면, "정책 없음" = "모든 조합 default-deny"가 자연스럽게 성립한다. None을 그대로 두면 나중에 `.get()`에서 터졌을 텐데, 그걸 안전한 빈 명단으로 만든다.

#### `warn_unknown_tools` — 오타를 가시화하는 안전망

```python
    def warn_unknown_tools(self, known: dict[str, set[str]]) -> None:
        for agent_id, servers in self.agents.items():
            for server, entries in (servers or {}).items():
                if server not in known:
                    logger.warning("policy: %s references unknown server %r", agent_id, server)
                    continue
                for entry in entries or []:
                    tool = entry["tool"] if isinstance(entry, dict) else entry
                    if tool not in known[server]:
                        logger.warning(
                            "policy: %s references unknown tool %r on server %r",
                            agent_id, tool, server,
                        )
```

- **의미**: YAML에 적힌 모든 (서버, tool) 이름을, 기동 시 백엔드에서 실제로 집계한 tool 목록(`known`)과 대조한다. 명단에 있는데 실제론 없는 이름이면 경고 로그를 찍는다.
- **왜? (default-deny의 함정 — eng review 이슈 5)**: 화이트리스트의 어두운 면이다. YAML에 `search_tikcets`처럼 **오타**를 내면, 그 규칙은 어떤 실제 tool과도 매칭이 안 돼 **조용히 거부**된다. 에러가 안 나니 운영자는 "분명 허용했는데 왜 막히지?"로 한참 헤맨다. 이 함수가 기동 시 한 번 대조해 그런 미존재 이름을 **로그로 드러낸다**.
- **왜? (warn이지 fail이 아님)**: 미스매치가 있어도 기동을 막지 않는다(경고만). 기동 시점에 다운된 백엔드는 `known`에 없어서 검증할 방법이 없으니, 정상 이름까지 오탐할 수 있다. 그래서 fail-fast가 아닌 **warn**으로 둔다 — 운영 가시성과 가용성 사이의 절충이다.
- **(보충)**: `tool = entry["tool"] if isinstance(entry, dict) else entry` — 1절에서 본 두 가지 항목 형태(문자열 vs dict)를 여기서도 동일하게 분기해 tool 이름을 꺼낸다.

#### `evaluate` — 매 요청마다 불리는 판정의 심장

```python
    def evaluate(self, agent_id: str, server: str, tool: str, args: dict) -> Decision:
        rule = f"{agent_id}:{server}:{tool}"
        entries = (self.agents.get(agent_id) or {}).get(server)
        for entry in entries or []:
            if isinstance(entry, dict):
                if entry.get("tool") == tool:
                    return self._check_args(rule, entry, args)
            elif entry == tool:
                return Decision(allowed=True, rule=rule)
        return Decision(allowed=False, rule=rule)
```

- **의미**: 한 번의 tool call이 허용되는지를 판정한다. ① 규칙 식별자를 먼저 만들고, ② 이 에이전트의 이 서버 허용 목록을 꺼내고, ③ 목록을 순회하며 일치 항목을 찾는다.
- **왜? (`rule`을 맨 먼저)**: 79행에서 `rule`을 가장 먼저 만든다. 허용이든 거부든 모든 반환 경로가 이 식별자를 필요로 하기 때문에, 한 번 만들어 재사용한다.
- **왜? (`(self.agents.get(agent_id) or {}).get(server)`)**: 미등록 `agent_id`면 `.get()`이 `None`을 반환하고, `or {}`가 빈 dict로 바꿔 `.get(server)`가 또 `None`을 준다. 결과적으로 `entries`가 `None`이 되고 → `for entry in entries or []`가 빈 루프 → **거부**로 떨어진다. **미등록 에이전트도 default-deny**가 코드 한 줄로 자연스럽게 보장된다 — 별도 if문 없이.
- **왜? (두 갈래 분기)**: 항목이 **dict**면 인자 제약이 붙은 허용 → tool 이름이 맞을 때 `_check_args`로 인자 검사를 위임한다. 항목이 **문자열**이면 제약 없는 허용 → 이름이 맞는 즉시 `Decision(allowed=True)`. 1절의 데이터 형태가 여기서 갈라진다.
- **왜? (마지막 줄)**: 어떤 항목과도 안 맞으면 = 미기재 = `Decision(allowed=False)`. **default-deny의 물리적 구현**이 바로 이 한 줄이다.

#### `_check_args` — 유일한 인자 레벨 정책

```python
    def _check_args(self, rule: str, entry: dict, args: dict) -> Decision:
        max_hours = entry.get("max_range_hours")
        if max_hours is None:
            return Decision(allowed=True, rule=rule)
        try:
            start = datetime.fromisoformat(args["start"])
            end = datetime.fromisoformat(args["end"])
            delta = end - start
        except (KeyError, TypeError, ValueError) as e:
            return Decision(allowed=False, rule=rule, detail=f"invalid time range: {e}")
        if delta.total_seconds() < 0:
            return Decision(allowed=False, rule=rule, detail="time range end before start")
        hours = delta.total_seconds() / 3600
        if hours > max_hours:
            return Decision(
                allowed=False, rule=rule, detail=f"time range {hours:g}h exceeds max {max_hours}h"
            )
        return Decision(allowed=True, rule=rule)
```

- **의미**: dict 항목에 붙은 인자 제약을 검사한다. 현재 지원하는 제약은 `max_range_hours` 하나뿐이다(analyst의 `query_logs` 24h).
- **왜? (`max_hours is None`이면 통과)**: dict 항목이지만 시간 제약이 없는 경우를 대비한 가드. tool 이름은 허용됐고 제약은 없으니 그냥 허용한다.
- **왜? (거부지 클램핑이 아님 — design.md "인자 레벨 정책")**: 25시간을 요청하면 24시간으로 **잘라 맞추지(clamp) 않고 거부**한다. 이 데모의 목적은 정책 위반을 **'가시화'**하는 것이다. 조용히 보정하면 보여줄 거부 장면이 사라진다. 그래서 위반은 또렷한 거부로 만든다.
- **왜? (세 가지 이상 입력을 모두 거부 — default-deny 일관성)**: `try/except`가 세 종류의 기형 입력을 한꺼번에 잡는다.
  - `KeyError`: `start`/`end` 인자 누락
  - `ValueError`: ISO8601로 파싱 불가능한 문자열
  - `TypeError`: naive datetime(타임존 없음)과 aware datetime(타임존 있음)을 빼면 발생
  
  셋 다 예외를 밖으로 흘리지 않고 **`POLICY_DENIED` + `detail`**로 변환한다. 판정이 불가능한 입력은 안전하게 거부(fail-closed)한다는 모듈 전체 원칙이 여기서도 일관된다.
- **왜? (`end < start` 별도 거부)**: 파싱은 됐지만 끝이 시작보다 앞선 무의미한 범위. 음수 delta를 따로 잡아 거부하고 사유를 `detail`에 싣는다.
- **(보충, `{hours:g}`)**: `:g` 포맷은 `25.0` 대신 `25`처럼 불필요한 소수점을 떼어 사람이 읽기 좋은 사유 메시지를 만든다.

---

## 3. Live wiring: 정책 평가는 어디서 불리고, 거부 payload는 누구와의 계약인가

이 두 파일은 자족적이지 않다 — 라우팅 경로와 거부 응답 계약의 양 끝에 물려 있다.

**평가 순서 (스펙 S4)**: 한 tool call은 정책 엔진에 닿기 전에 두 관문을 거친다.
1. **인증** — JWT 검증. 누구인지(`agent_id`) 먼저 확정.
2. **tool 해석** — 요청한 tool이 실제로 존재하는지 확인. 없으면 `UNKNOWN_TOOL`로 거부(정책까지 가지 않는다 — 미존재 tool은 정책 위반이 아니라 *그런 tool이 없음*이다).
3. **정책 평가** — 여기서 비로소 `Policy.evaluate`가 불린다.

> 이 순서가 중요한 이유: 미존재 tool은 `POLICY_DENIED`가 아니라 `UNKNOWN_TOOL`이다. 둘을 섞으면 에이전트가 "권한이 없는 건가, tool 이름을 틀린 건가"를 구분 못 한다. 정책은 *실재하는* tool에 대한 *허가*만 판정한다.

**거부 payload 계약 (S6 데모와의 고정 인터페이스)**: `evaluate`가 `allowed=False`를 돌려주면, 라우팅 계층이 그것을 고정 형식의 JSON으로 변환한다(payload 생성은 `errors.py` 헬퍼 재사용).

```json
{ "code": "POLICY_DENIED", "rule": "<agent>:<server>:<tool>", "agent": "<agent_id>" }
```

인자 레벨 위반이면 `detail` 필드가 추가로 붙는다(예: `"time range 25h exceeds max 24h"`). 이 형식이 **고정 계약**인 이유: S6 데모/audit admin 페이지가 `rule`과 `agent`를 파싱해 "누가·어디서·무엇이 막혔는지"를 화면에 보여주기 때문이다. `Decision.rule`이 `"agent:server:tool"` 형식인 것(2절)이 바로 이 계약을 위한 것이었다.

---

## 4. 관통하는 설계 원칙 요약

- **Default-deny (화이트리스트)** — 명시 허용만 통과, 나머지는 전부 거부. 미등록 에이전트·미기재 조합은 코드의 특별 분기 없이 자연스럽게 차단된다. 빠뜨림이 사고가 아니라 안전이다.
- **데이터/코드 분리** — "누가 무엇을"은 `policy.yaml`(운영 정책)에, "어떻게 판정하나"는 `policy.py`(엔진)에. 권한 변경은 코드 재배포 없이 YAML 한 줄로 끝난다.
- **Fail-closed (의심스러우면 거부)** — `evaluate`는 어떤 기형 입력에도 예외를 던지지 않고 항상 `Decision`을 반환한다. 파싱 불가·역순 범위·tz 혼용 셋 다 조용히 거부로 수렴한다.
- **거부는 클램핑이 아니라 가시화** — 24h 위반을 24h로 잘라 맞추지 않고 또렷이 거부한다. 데모의 가치는 위반 장면을 *보여주는* 데 있다.
- **차등 제약 표현력** — 같은 `query_logs`라도 analyst엔 24h 제약, dev엔 무제약. 정책 엔진이 단순 on/off를 넘어 에이전트별 인자 제약까지 표현한다.
- **오타 가시성 (warn-not-fail)** — default-deny는 오타를 조용한 거부로 숨긴다. `warn_unknown_tools`가 기동 시 한 번 대조해 그 함정을 로그로 드러내되, 기동은 막지 않는다.
- **계약으로서의 출력** — `Decision.rule`의 `"agent:server:tool"` 형식과 `POLICY_DENIED` payload는 S6 데모·audit 페이지가 파싱하는 고정 인터페이스다. 거부 응답은 부산물이 아니라 명세된 산출물이다.
