# 학습 자료: `JWT 인증` 완전 해부

> 대상: `gateway/src/gateway/auth.py`, `scripts/issue_tokens.py`
> 목적: "Bearer 토큰 한 장으로 누가 들어왔는지(agent_id)를 알아내는" 인증 계층이 어떻게/왜 이렇게 단순하게 설계됐는지를 비개발자도 추적할 수 있게 해부한다.
> 관련 스펙: `docs/specs/04-auth-policy-audit.md`, `docs/design/agentops-gateway-design.md`

---

## 0. 큰 그림

### ASCII 다이어그램 — 토큰의 일생

```
  [발급]                          [전송]                       [검증]
 issue_tokens.py                 클라이언트/에이전트            gateway/auth.py
 ┌───────────────┐              ┌────────────────────┐        ┌──────────────────┐
 │ secret(env)   │              │ HTTP 요청          │        │ secret(env)      │
 │  + agent_id   │── 토큰 ──►   │ Authorization:     │── ► ── │ jwt.decode()     │
 │  + exp(+30일) │  (stdout에   │   Bearer <token>   │ 헤더   │  서명 검증 +exp   │
 │  HS256 서명   │   찍어줌)    │                    │        │  agent_id 추출   │
 └───────────────┘              └────────────────────┘        └────────┬─────────┘
        ▲                                                              │
        │  같은 GATEWAY_JWT_SECRET 을 공유해야만 검증이 성립           ▼
        └──────────────────────────────────────────────►   "support-agent"  (또는 AuthError)
```

### 내러티브 — "사원증과 검문소" 메타포

회사 출입을 떠올려 보자. **`issue_tokens.py`는 사원증 발급기**다. 단, 이 데모에서는
출입증을 매번 새로 찍어 주는 정식 발급실(로그인 창구, 재발급 시스템)을 두지 않는다.
대신 셋업 단계에서 에이전트 3명분(`support-agent`, `analyst-agent`, `dev-agent`)의
사원증을 미리 한 번에 인쇄해 책상 위에 올려 두는 식이다. 사용자는 그 카드를 복사해
요청마다 들고 다닌다.

**`gateway/auth.py`는 정문 검문소**다. 들어오는 모든 사람에게 "사원증 보여 주세요"라고
요구하고(`Authorization: Bearer <token>` 헤더), 그 카드가 **우리 회사가 발급한 진짜인지**
(서명 검증), **유효기간이 지나지 않았는지**(exp 검사)를 확인한다. 통과하면 카드에 적힌
이름표(`agent_id`)를 읽어 "아, support-agent 님이군요"라고 신원을 확정한다. 통과하지
못하면 사유를 셋 중 하나로 딱 잘라 돌려준다 — 카드를 안 가져왔거나(`missing`),
위조/손상됐거나(`invalid`), 기간이 지났거나(`expired`).

핵심은 **발급기와 검문소가 같은 비밀 도장(`GATEWAY_JWT_SECRET`)을 공유**한다는 점이다.
이 도장이 같아야만 검문소가 "이 카드는 우리가 찍은 게 맞다"고 판단할 수 있다. 그래서
두 파일은 떨어져 있어도 사실 한 쌍이며, 이 프로젝트는 토큰이 누구인지만 확인할 뿐
(인증), "그래서 무엇을 할 수 있는가"(인가/정책)는 그 다음 단계가 따로 맡는다.

### 구조 매핑 테이블

| 파일 | 역할 | 메타포 |
| --- | --- | --- |
| `scripts/issue_tokens.py` | 정적 토큰 3장 생성 → stdout 출력 | 사원증 발급기 (셋업 때 1회 인쇄) |
| `gateway/src/gateway/auth.py` | Authorization 헤더 검증 → `agent_id` 반환 | 정문 검문소 |
| 공유 자원 `GATEWAY_JWT_SECRET` (env) | 서명/검증에 쓰는 대칭키 | 회사 인장 (발급·검문이 동일) |

---

## 1. `scripts/issue_tokens.py` — 토큰 발급기

### 배경: 왜 발급 "서버"가 아니라 스크립트 한 장인가

설계 결정(`design.md` 구현 명세)은 명확하다. **이 데모는 토큰 발급 인프라
(로그인·회전·발급 API)를 만들지 않는다.** 그건 프로젝트의 핵심 가치인 *권한 매트릭스
enforcement*(3 에이전트 × 3 서버를 tool call 단위로 강제)와 무관한 곁가지이기 때문이다.
대신 스크립트가 토큰 3장을 미리 찍어 주고, 사용자는 복사해서 헤더에 붙인다. 더 중요한 건
**테스트 픽스처도 이 `issue_token` 함수를 그대로 재사용**한다는 점이다 — "발급 방식"이
한 곳에서만 정의되므로 "발급 = 검증"의 짝이 어긋날 수 없다.

### 줄별 해설

```python
# 권한 매트릭스의 3 에이전트 — 이 agent_id가 곧 정책 YAML의 키이자 audit/메트릭의 라벨.
AGENTS = ["support-agent", "analyst-agent", "dev-agent"]
```

- **의미**: 이 코드는 토큰을 찍어 줄 에이전트 3명의 이름을 못박는다.
- **왜?**: 이 문자열은 단순한 이름이 아니라 **시스템 전체를 관통하는 식별자**다. 똑같은
  `agent_id`가 정책 YAML의 키로, audit 로그·메트릭의 라벨로 재등장한다. 그래서 임의로
  바꾸면 안 되는 계약값이다.

```python
def issue_token(agent_id: str, secret: str, days: int = 30) -> str:
    """HS256 토큰을 만든다. claim은 {"agent_id", "exp"} 둘 뿐 — Gateway auth가 보는 게 그게 전부다."""
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    return jwt.encode({"agent_id": agent_id, "exp": exp}, secret, algorithm="HS256")
```

- **의미**: 이 코드는 "지금부터 30일 뒤에 만료된다"는 시각(`exp`)과 신원(`agent_id`)을
  담은 JWT를 `secret`으로 서명해 문자열 한 장으로 만든다.
- **왜?**: claim을 딱 둘(`agent_id` + `exp`)로 둔 건 **검증 측이 보는 게 그게 전부**라서다
  (1절에서 `auth.py`가 정확히 이 두 값만 읽는 걸 확인하게 된다). 군더더기 claim을 넣지
  않는 "Simplicity First"의 실천이다.
- **왜 30일?**: 만료 30일은 순전히 데모 편의다 — 매번 재발급하지 않아도 되게. 운영이라면
  짧은 수명 + 회전이 정석이지만, 그건 이 데모의 범위 밖이라고 코드 docstring이 직접 밝힌다.
- (`jwt.encode`는 pyjwt 라이브러리. `HS256`은 대칭키 방식 — 발급과 검증이 *같은* secret을
  쓴다는 뜻이며, 그래서 secret 공유가 전제다.)

```python
if __name__ == "__main__":
    # secret은 검증 측(gateway.auth)과 반드시 같아야 한다 — 같은 env var를 공유하는 이유.
    secret = os.environ["GATEWAY_JWT_SECRET"]
    for agent in AGENTS:
        print(f"{agent}: {issue_token(agent, secret)}")
```

- **의미**: 이 코드는 스크립트를 직접 실행했을 때 env에서 secret을 읽어, 3명분 토큰을
  `이름: 토큰` 형태로 화면에 출력한다.
- **왜?**: `os.environ[...]`(대괄호 직접 접근)은 secret이 없으면 **조용히 빈 값으로
  가는 대신 즉시 에러로 터뜨리려는** 의도다. secret 없이 발급되는 토큰은 검문소가 절대
  통과시키지 못할 쓸모없는 카드이기 때문에, 일찍 실패하는 편이 안전하다.
- **왜 stdout 출력?**: 레포에 실제 운영 secret이나 토큰을 파일로 두지 않기 위해서다.
  사용자가 그때그때 secret을 주입해 찍고, 화면의 결과를 복사해 쓴다.
- (실행 예: `GATEWAY_JWT_SECRET=<secret> uv run python scripts/issue_tokens.py`)

---

## 2. `gateway/src/gateway/auth.py` — 검문소

### 배경: 왜 검증만 하고, 실패 사유를 3종으로 고정했나

`auth.py`는 의도적으로 단순하다. 발급(로그인·회전·발급 API)을 일부러 범위에서 뺐기
때문에, 이 파일이 하는 일은 "들어온 카드가 진짜이고 안 만료됐는지 확인 → 이름 읽기"가
전부다. 그리고 실패 사유를 `missing | invalid | expired` **셋으로만 고정**한다. 이 세
문자열은 임의 메시지가 아니라 **`AUTH_FAILED` payload의 `reason` 필드 계약**이다.
하류의 S6 에이전트·테스트가 이 값을 보고 분기하므로, 안정적인 enum이어야 한다.

(인증 실패는 호출 종류에 따라 두 계층으로 표현된다 — `tools/call`이면 MCP
`isError:true` + `{"code":"AUTH_FAILED","reason":...}`, 그 외 요청(`initialize`,
`tools/list`)이면 HTTP 401 + 동일 JSON body. 이 분기는 `app.py`가 `AuthError.reason`을
받아 처리하며, `auth.py`는 *사유만 정확히 던지는* 역할에 집중한다.)

### 줄별 해설

```python
class AuthError(Exception):
    """인증 실패. reason에 안정적 사유 코드를 담아 호출처(app.py)가 응답으로 변환한다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # missing | invalid | expired — AUTH_FAILED payload 계약
```

- **의미**: 이 코드는 "인증 실패"라는 사건을 `reason` 한 줄과 함께 들고 다니는 전용
  예외 타입을 정의한다.
- **왜?**: 호출처(`app.py`)가 `except AuthError`로 잡아 `err.reason`을 그대로
  응답 payload에 꽂을 수 있게 하기 위해서다. 사유를 문자열 메시지로 흘리지 않고 **구조화된
  필드**로 박아 둬, 하류가 안정적으로 분기한다.

```python
def authenticate(authorization: str | None) -> str:
    """Authorization 헤더를 검증해 agent_id 문자열을 반환. 실패 시 AuthError(reason)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing")
    token = authorization.removeprefix("Bearer ")
```

- **의미**: 이 코드는 헤더가 비었거나 `Bearer `로 시작하지 않으면 카드 자체가 없는
  것(`missing`)으로 보고, 형식이 맞으면 접두사를 떼어내 순수 토큰만 남긴다.
- **왜?**: "카드를 안 가져온 사람"과 "위조 카드를 낸 사람"을 처음부터 구분하기 위해서다.
  토큰 형식조차 아닌 건 검증 라이브러리로 보낼 가치도 없으므로 여기서 `missing`으로 즉시
  차단한다.

```python
    try:
        claims = jwt.decode(token, os.environ["GATEWAY_JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthError("expired") from None
    except jwt.InvalidTokenError:
        raise AuthError("invalid") from None
```

- **의미**: 이 코드는 토큰의 **서명 검증과 exp 만료 검사를 한 번에** 수행한다.
  `jwt.decode`가 이 둘을 동시에 처리하며, 통과하면 claim 딕셔너리를 돌려준다.
- **왜 secret을 `os.environ[...]`로 직접?**: 발급 측과 *같은 secret*이어야 검증이
  성립하기 때문이며(HS256 대칭키), secret 미설정이면 `KeyError`로 즉시 터지게 둔다 —
  secret 없이 가동되는 건 설정 사고라 조용히 통과시키지 않는다.
- **왜 예외를 둘로 나눠 잡나?**: 만료(`ExpiredSignatureError`)는 서명은 멀쩡하지만
  기간만 지난 "우회 불가 사유"라 `expired`로 따로 표시하고, 그 외 모든 토큰 문제(서명
  불일치·형식 오류 등)는 `InvalidTokenError`로 묶여 `invalid`가 된다. 사유를 정확히
  구분해야 하류 진단이 명확해진다.
- (`from None`은 내부 pyjwt 예외의 장황한 트레이스백을 숨기고 `AuthError`만 깔끔히
  표면화하는 관용구다.)

```python
    agent_id = claims.get("agent_id")
    if not agent_id:
        raise AuthError("invalid")
    return agent_id
```

- **의미**: 이 코드는 서명·만료를 통과한 토큰에서 이름표(`agent_id`)를 꺼내고, 그게
  비어 있으면 `invalid`, 있으면 그 문자열을 반환한다.
- **왜?**: 서명은 유효해도 우리 계약(`agent_id` claim)을 안 따르는 토큰은 신원을 알 수
  없으므로 `invalid`로 취급한다. 이 반환값 `agent_id`가 곧 **이후 정책 평가·audit의
  주체**가 된다 — 검문소의 최종 산출물이다.

---

## 3. Live wiring: 인증이 실제로 불리는 지점

(아래 연결은 스펙 근거이며, 호출 측 코드는 본 문서의 담당 파일 밖이라 인용하지 않고
경로/계약만 짚는다.)

- **요청 경로**: 클라이언트 → Gateway(`app.py`)가 모든 요청에서 `Authorization` 헤더를
  꺼내 `authenticate(...)`에 넘긴다. 평가 순서는 **① 인증 → ② tool 해석 → ③ 정책 평가**.
  즉 인증이 가장 먼저, 모든 진입을 막아서는 첫 관문이다.
- **`agent_id`의 행선지**: `authenticate`가 돌려준 문자열은 이후 정책 YAML 조회의 키,
  그리고 audit 로그·메트릭의 라벨로 흐른다. `issue_tokens.py`의 `AGENTS` 상수가 이 값의
  단일 출처다.
- **실패의 두 얼굴**: `AuthError.reason`을 받은 `app.py`가 호출 종류로 분기한다 —
  `tools/call`은 MCP `isError:true` + `{"code":"AUTH_FAILED","reason":...}`,
  비-tool-call(`initialize`, `tools/list`)은 HTTP 401 + 동일 JSON body. `tools/list`는
  **인증은 요구하되 정책 필터링은 없다**(누구인지는 확인하지만 무엇을 볼 수 있는지는 안
  거름).
- **테스트/데모에서의 토큰**: 테스트 픽스처가 `issue_token`을 그대로 재사용해 토큰을
  찍어 헤더에 실어 보낸다. 발급과 검증이 같은 함수·같은 secret을 공유하므로, 테스트가
  통과하면 데모 동작도 같은 보장을 받는다.

---

## 4. 관통하는 설계 원칙 요약

- **발급은 빼고 검증만 — 범위의 칼질**: 핵심 가치(권한 enforcement)와 무관한 발급
  인프라(로그인·회전·발급 API)를 의도적으로 제거하고, 스크립트 1장 + 검증 함수 1개로
  최소화했다.
- **"발급 = 검증"을 한 곳에서 정의**: 테스트 픽스처가 `issue_token`을 재사용해, 발급
  방식과 검증 방식이 어긋날 수 없는 단일 출처 구조를 만든다.
- **secret은 공유·필수·env 전용**: HS256 대칭키라 발급·검증이 같은
  `GATEWAY_JWT_SECRET`을 써야 하며, 미설정이면 `KeyError`로 일찍 터뜨려 설정 사고를
  숨기지 않는다. 운영 secret은 레포에 두지 않는다.
- **실패 사유는 안정적 enum 3종**: `missing | invalid | expired`는 단순 메시지가 아니라
  `AUTH_FAILED` payload의 `reason` 계약이며, 하류 에이전트·테스트가 이 값으로 분기한다.
- **claim은 최소(`agent_id` + `exp`)**: 검증 측이 보는 것만 담아 군더더기를 배제했다 —
  Simplicity First의 직접 실천.
- **인증은 첫 관문, agent_id는 그 산출물**: 평가 순서상 인증이 가장 먼저 실행되고, 그
  결과인 `agent_id`가 이후 정책·audit 전부를 끌고 가는 주체가 된다.
- **인증과 인가의 분리**: 이 계층은 "누구인가"만 판정한다. "무엇을 할 수 있는가"(정책)는
  명확히 다음 단계의 책임으로 남겨, 각 계층이 한 가지 일만 하게 했다.
