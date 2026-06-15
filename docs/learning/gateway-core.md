# 학습 자료: `Gateway 코어` 완전 해부

> 대상: `gateway/src/gateway/{app,aggregate,upstream,routes,errors,__main__}.py`
> 목적: 에이전트가 외부 도구에 접근할 때 거치는 단일 관문(Gateway)이 "인증 → 정책 → 라우팅 → 관측·감사"를 한 경로로 어떻게 강제하는지, 그 코어 6개 파일을 비개발자도 따라올 수 있게 줄별로 해부한다.
> 관련 스펙: `docs/specs/03-gateway-core.md`, `docs/design/agentops-gateway-design.md`

---

## 0. 큰 그림

```
            [ AI 에이전트 (클라이언트) ]
                      │  POST /mcp  (JWT 헤더 첨부)
                      ▼
   ┌──────────────────────────────────────────────┐
   │              GATEWAY  (:8000)                  │
   │  ┌────────────────────────────────────────┐    │
   │  │ MCPEndpoint  ── 바깥 인증 껍데기(401/통과) │    │
   │  └──────────────┬─────────────────────────┘    │
   │   call_tool 단일 경로:                          │
   │     1) auth   →  2) routes.route_call           │
   │                     ├─ aggregate.split (prefix) │
   │                     ├─ policy.evaluate          │
   │                     └─ Backend.call (중계)       │
   │     3) observability  4) audit                  │
   └───────┬─────────────┬─────────────┬────────────┘
           │ ticket__    │ docs__      │ ops__
           ▼             ▼             ▼
      [:8101 ticket] [:8102 docs] [:8103 ops]   ← 백엔드 MCP 3종
```

Gateway는 **공항 보안 검색대** 같은 존재다. 에이전트(승객)는 백엔드 도구(탑승구)로 직행하지 못하고 반드시 이 검색대를 통과한다. 검색대 하나만 지키면 "누가(인증) 어디로(라우팅) 갈 자격이 있는지(정책)"를 한곳에서 빠짐없이 통제할 수 있다. 만약 승객이 탑승구로 직행할 수 있다면 도구 호출 한 건 한 건을 감시할 방법이 없어진다 — 이것이 `design.md`가 말하는 "직접 연결 구조에선 tool call 단위 통제가 불가능"의 의미다.

이 코어가 푸는 세 가지 문제는 이렇게 나뉜다. 첫째, **이름 충돌**: 백엔드 3종이 각자 `search_tickets`, `search_docs`를 짓는데, 합치면 출처를 알 수 없다 → `aggregate.py`가 `ticket__`, `docs__` 같은 prefix를 붙여 네임스페이싱한다. 둘째, **연결 관리**: 백엔드와 매번 새로 연결하면 느리고 불안정하다 → `upstream.py`가 백엔드당 세션 1개를 열어 재사용한다. 셋째, **응답 계약**: 거부·오류를 에이전트가 기계적으로 읽을 수 있어야 한다 → `errors.py`가 모든 오류 봉투를 한 함수로만 찍어낸다.

핵심은 **요청 경로가 딱 하나**라는 점이다. 모든 tool 호출은 `app.py`의 `call_tool` 함수 한 곳을 지난다. 미들웨어 체인 같은 추상화를 일부러 피했다 — 경로가 하나뿐이면 추상화의 이득보다 추적 난이도만 늘기 때문이다(`CLAUDE.md` "Simplicity First").

| 파일 | 역할 (한 줄) |
| --- | --- |
| `app.py` | 단일 진입점 조립. `call_tool` 한 경로에 인증 → 정책·라우팅 → 관측 → 감사를 직렬로 끼운다. |
| `aggregate.py` | tool 이름 prefix 네임스페이싱(`ticket__create_ticket`)과 `tools/list` 집계. |
| `upstream.py` | 백엔드당 MCP 세션 1개를 소유 task로 열어 재사용·재연결 관리. |
| `routes.py` | `tools/call` 라우팅: tool 해석 → 정책 평가 → 백엔드 중계. |
| `errors.py` | `isError` 오류 payload 스키마의 단일 진실 지점. |
| `__main__.py` | `python -m gateway` 실행 진입점 — uvicorn으로 `:8000` 서빙. |

---

## 1. `errors.py` — 오류 응답의 단일 진실 지점

### 배경: 왜 이렇게 설계했나

Gateway가 내는 모든 거부·오류 응답은 에이전트가 **기계적으로 파싱**할 수 있어야 한다. `design.md`의 "거부 응답 형식" 계약에 따르면, S6의 LangGraph 에이전트는 거부를 받고 그 사유를 읽어 우회 계획을 세운다. 그러려면 오류 봉투 형식이 코드 곳곳에 흩어져선 안 되고 한 함수로만 생성돼야 한다 — 형식이 한 곳에만 있으면 계약이 깨질 일이 없다. 가장 먼저 이 파일을 보는 이유는, 나머지 모든 파일이 거부할 때 이 함수를 호출하기 때문이다.

### 줄별 해설

```python
def error_result(code: str, **fields: str) -> types.CallToolResult:
    payload = {"code": code, **fields}
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=json.dumps(payload))],
    )
```

- **의미**: `code`(예: `"POLICY_DENIED"`)와 호출처가 자유롭게 붙이는 추가 필드(`**fields`, 예: `rule`, `agent`)를 `{"code": ..., ...}` 한 봉투로 묶어, `isError=True`인 MCP tool result로 만든다. 본문은 `content[0].text`에 JSON 문자열 한 블록으로 직렬화된다.
- **왜?**: "tool이 실패했다"를 HTTP 에러나 파이썬 예외로 던지면 transport 계층에서 끊겨 에이전트가 구조화된 사유를 못 받는다. MCP에서 실패는 **정상적인 응답 형태**(`isError=true` result)이므로, result로 돌려줘야 에이전트가 `content[0].text`의 JSON을 파싱해 `code`/`rule`/`reason`을 읽고 다음 행동을 결정할 수 있다.
- (이 함수 하나가 찍어내는 코드들 — 스프린트별: `UNKNOWN_TOOL`/`BACKEND_UNAVAILABLE`은 S3 라우팅·중계, `POLICY_DENIED`/`AUTH_FAILED`는 S4 인증·정책, `RATE_LIMITED`는 S5 관측. 전부 같은 봉투를 쓴다.)

(메타포: 이 함수는 **공식 양식 발급기**다. 어느 부서가 반려하든 반려 사유서는 한 양식으로만 나오므로, 받는 쪽은 양식 읽는 법을 한 번만 배우면 된다.)

---

## 2. `aggregate.py` — prefix 네임스페이싱과 tools/list 집계

### 배경: 왜 이렇게 설계했나

백엔드 3종은 각자 독립적으로 tool 이름을 짓는다. Gateway가 이들을 한 목록으로 합치면 이름 충돌·출처 불명 문제가 생긴다. 그래서 서버 prefix를 붙여 `ticket__search_tickets`처럼 네임스페이싱한다. 이 prefix는 세 가지 역할을 **동시에** 한다: (1) 집계 시 출처 표시, (2) 호출 시 어느 백엔드로 보낼지 결정하는 라우팅 키(`routes.py`), (3) 메트릭·audit 라벨의 `server` 차원.

### 줄별 해설

```python
SEPARATOR = "__"
```

- **의미**: 서버와 tool 이름을 잇는 구분자를 더블 언더스코어로 고정.
- **왜?**: 일반 tool 이름(snake_case)에 단일 `_`는 흔하지만 `__`는 거의 없어 충돌 위험이 낮다. 구분자가 tool 이름 안에 우연히 등장하면 잘못 쪼개지므로, "거의 안 나오는 시퀀스"를 예약한 것이다.

```python
def split(name: str) -> tuple[str, str] | None:
    if SEPARATOR not in name:
        return None
    server, _, tool = name.partition(SEPARATOR)
    return server, tool
```

- **의미**: `'ticket__create_ticket'`을 `('ticket', 'create_ticket')`으로 되돌린다. 구분자가 없으면 `None`.
- **왜?**: `None`은 "이 이름은 우리가 prefix를 붙인 게 아니다"라는 신호 — 라우팅에서 `UNKNOWN_TOOL`로 이어진다. `partition`은 **첫** `SEPARATOR`에서만 자르므로 tool 쪽에 `__`가 또 있어도 안전하다(서버 이름만 정확히 떼어낸다).

```python
def prefixed(server: str, tools: list[types.Tool]) -> list[types.Tool]:
    return [tool.model_copy(update={"name": join(server, tool.name)}) for tool in tools]
```

- **의미**: 백엔드가 준 tool 목록의 `name`에 server prefix를 붙인 **새** Tool 리스트를 만든다.
- **왜?**: `model_copy(update=...)`로 이름만 바꾼 복사본을 만든다. 원본 Tool(스키마·설명 등)은 그대로 두고 `name` 필드만 교체해야 백엔드 캐시(`backend.tools`)를 오염시키지 않는다. (원본을 직접 고치면 다음 집계 때 `ticket__ticket__...`처럼 prefix가 이중으로 붙는 버그가 난다.)

```python
async def aggregate_tools(backends: dict) -> list[types.Tool]:
    out: list[types.Tool] = []
    for backend in backends.values():
        if backend.tools is None:
            try:
                await backend.ensure_session()
            except Exception:
                continue
        out.extend(prefixed(backend.name, backend.tools or []))
    return out
```

- **의미**: 전 백엔드의 tool 목록을 prefix 붙여 하나로 합쳐 `tools/list` 응답으로 돌려준다.
- **왜? (핵심 설계 — 정책으로 필터링하지 않는다)**: 여기서 "이 에이전트가 못 쓰는 tool"을 목록에서 빼지 **않는다**. 모든 에이전트가 7개 전체(`ticket__create_ticket`, `ticket__search_tickets`, `ticket__update_status`, `docs__search_docs`, `docs__read_doc`, `ops__get_metrics`, `ops__query_logs`)를 본다. 의도된 데모 설계다: support-agent가 ops tool의 **존재**를 알아야 S6의 "호출 시도 → 정책 거부 → 우회 계획" 장면이 자연스럽게 발생한다. **거부는 목록이 아니라 '호출 시점'에 일어난다.** (production이라면 정책 기반 목록 필터링이 기본값이어야 함 — README에 명시.)
- **왜? (지연 재집계)**: `backend.tools is None`은 "아직 한 번도 집계 못 한" 백엔드다(기동 시 죽어 있었음). 이 시점에 `ensure_session()`으로 다시 붙기를 시도하고, 그래도 실패하면 `continue`로 조용히 건너뛴다 — 살아있는 백엔드의 tool은 정상 노출하고, 죽은 백엔드는 다음 `tools/list` 때 또 시도한다(부분 가용성).

(메타포: prefix는 **이름표 앞에 붙인 부서명 스티커**다. 같은 "검색" 직원이 부서마다 있어도 "티켓팀-검색", "문서팀-검색"으로 부르면 헷갈리지 않고, 나중에 그 스티커만 보고 누구에게 일을 넘길지(라우팅)도 정해진다.)

---

## 3. `upstream.py` — 백엔드당 세션 1개, 소유 task로 관리

### 배경: 왜 풀이 아니라 단일 세션 + 소유 task인가

이 파일에서 가장 중요한 설계다. MCP 클라이언트 연결 컨텍스트(`streamablehttp_client` / `ClientSession`)는 내부에 anyio **cancel scope**를 품고 있고, anyio에는 강한 규칙이 있다: **'진입한 task에서만 탈출(close)할 수 있다'**. 요청을 처리하는 여러 task가 같은 컨텍스트를 자유롭게 enter/exit하면 `"cancel scope in different task"` 런타임 오류가 난다.

그래서 구조를 이렇게 잡았다: 연결마다 **소유 task** 하나(`_connection_task`)를 띄워 컨텍스트의 진입·유지·탈출을 전부 그 task 안에서만 하고, 실제 요청 task들은 컨텍스트를 건드리지 않고 `session` 객체만 공유한다. 이 동시 호출 안전성은 Day-1 스파이크(`scripts/spike_concurrency.py`)로 검증했다. (커넥션 풀이 아니라 **단일 세션 공유**임에 유의.)

### 줄별 해설

```python
class Backend:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.tools: list[types.Tool] | None = None
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._lock = (asyncio.Lock())
```

- **의미**: 백엔드 하나의 상태를 들고 있는 객체. `tools`는 캐시된 tool 목록, `_session`은 요청 task들이 공유해 쓰는 살아있는 세션, `_task`는 연결 컨텍스트를 소유·유지하는 task, `_stop`은 그 task에게 "닫아라"를 알리는 신호, `_lock`은 연결/재연결을 직렬화하는 자물쇠.
- **왜?**: `tools is None`이 "미집계(지연 재집계 대상)"의 단일 표식이다. `_lock`은 동시 요청이 세션을 **중복 생성**하지 못하게 막는다.

```python
    async def _connection_task(self, ready, stop) -> None:
        try:
            async with streamablehttp_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.tools = (await session.list_tools()).tools
                    self._session = session
                    ready.set_result(None)
                    await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else ConnectionError(str(e)))
        finally:
            self._session = None
```

- **의미**: 연결 컨텍스트의 **소유 task**. 컨텍스트에 진입 → MCP 핸드셰이크(`initialize`) → tool 목록 캐시 → 세션 공개(`self._session = session`) → `ready` future로 "성공" 통지 → `stop.wait()`로 멈춰 서서 컨텍스트를 열어 둔 채 대기한다.
- **왜?**: `await stop.wait()`가 이 설계의 심장이다. 이 task는 일을 하지 않고 **그냥 연결을 살려 두려고 대기**만 한다. 컨텍스트의 close는 이 task가 `stop` 신호를 받고 빠져나올 때 `with` 블록 종료로 **자연히** 일어난다(다른 task가 닫지 않는다 → cancel scope 규칙 준수). 연결 실패 시엔 아직 `ready`를 못 채웠으면 예외를 실어 호출자에게 전파한다.

```python
    async def _race_call(self, session, owner, tool, arguments) -> types.CallToolResult:
        call = asyncio.ensure_future(session.call_tool(tool, arguments))
        done, _ = await asyncio.wait({call, owner}, return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            return call.result()
        call.cancel()
        raise ConnectionError(f"backend {self.name} connection lost")
```

- **의미**: 실제 `call_tool`을 **소유 task(`owner`)의 종료와 경주**시킨다. 둘 중 먼저 끝나는 쪽을 본다. `call`이 먼저 끝나면 정상 응답을 반환하고, `owner`가 먼저 끝나면(=연결이 죽음) 매달린 call을 취소하고 `ConnectionError`를 던진다.
- **왜?**: 백엔드가 갑자기 죽으면 transport의 task group이 취소로 무너지는데, 이때 세션이 기다리던 응답 통지도 함께 취소돼 `call_tool`이 **'영원히 깨어나지 못하는' 교착**에 빠질 수 있다. 경주를 붙여 연결 소실을 빠르게 감지·실패시킨다.

```python
    async def call(self, tool: str, arguments: dict) -> types.CallToolResult:
        try:
            session = await self.ensure_session()
            return await self._race_call(session, self._task, tool, arguments)
        except Exception:
            async with self._lock:
                await self._teardown_locked()
                try:
                    await self._connect_locked()
                except Exception:
                    logger.warning("backend %s unavailable", self.name)
                    return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)
                session = self._session
            try:
                return await self._race_call(session, self._task, tool, arguments)
            except Exception:
                async with self._lock:
                    await self._teardown_locked()
                logger.warning("backend %s unavailable after reconnect", self.name)
                return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)
```

- **의미**: `tools/call`을 백엔드로 중계한다. 첫 시도가 실패하면 **락 안에서** 완전히 끊고(`_teardown_locked`) **딱 한 번** 재연결(`_connect_locked`)한 뒤 다시 호출한다. 재연결 자체가 실패하거나 재연결 후 호출도 실패하면 `BACKEND_UNAVAILABLE` 오류 result를 돌려준다.
- **왜? (재시도를 '1회'로 둔 이유)**: 무한/다회 재시도는 죽은 백엔드에 대한 요청을 길게 매달아 latency를 악화시키고 장애를 전파한다. 한 번 재연결해 보고 그래도 안 되면 구조화된 `BACKEND_UNAVAILABLE`을 **빠르게** 돌려주는 게 데모·운영 모두에 낫다. (재연결·teardown이 전부 `self._lock` 뒤에서 직렬화됨에 유의 — 동시 요청이 서로의 재연결을 망가뜨리지 못한다.)

```python
    async def ensure_session(self) -> ClientSession:
        async with self._lock:
            if self._session is None:
                await self._teardown_locked()
                await self._connect_locked()
            return self._session
```

- **의미**: 살아있는 세션을 반환. 없으면 잔재를 청소하고 새로 연결한다.
- **왜?**: `_lock`으로 감싸 동시 요청이 들어와도 연결은 **한 번만** 생성된다 — 나머지 요청은 락 뒤에서 기다렸다가 이미 만들어진 세션을 받는다(중복 연결 방지).

(메타포: 백엔드 연결은 **상점과 이어진 전용 직통 전화선**이다. 손님(요청)마다 새 전화를 거는 대신 한 회선을 항상 열어 둔다. 그 회선을 처음 연 직원(소유 task)만 끊을 수 있고, 다른 직원들은 그 회선에 대고 말만 한다. 회선이 끊기면 한 번 다시 걸어보고, 그래도 안 되면 "지금 연결 불가" 안내문을 빠르게 내민다.)

---

## 4. `routes.py` — tool 해석 → 정책 평가 → 백엔드 중계

### 배경: 왜 평가 순서를 고정했나 (eng review 이슈 2)

정책 평가는 server·tool이 **확정된 후에만** 한다. 순서가 핵심이다: 존재하지 않는 tool은 `POLICY_DENIED`가 아니라 `UNKNOWN_TOOL`로 응답해야 한다. 만약 정책을 먼저 보면, 오타 난 tool 이름이 "정책에 없으니 거부"(`POLICY_DENIED`)로 잘못 분류돼 운영자가 **권한 문제로 오해**한다. 그래서 "이 tool이 실제로 있나?"를 먼저 확인하고, 있는 tool에 대해서만 정책을 묻는다. (인증은 이미 `app.py`에서 끝난 뒤 이 함수가 호출된다.)

### 줄별 해설

```python
async def route_call(backends, policy, agent, name, arguments) -> tuple[types.CallToolResult, str]:
    parts = aggregate.split(name)
    if parts is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    server, tool = parts
    backend = backends.get(server)
    if backend is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    if backend.tools is None:
        try:
            await backend.ensure_session()
        except Exception:
            return error_result("BACKEND_UNAVAILABLE", server=server), "error"
    if tool not in {t.name for t in backend.tools or []}:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
```

- **의미**: 네 단계 관문이다. (1) prefix 분해 — 없으면 우리가 만든 이름이 아니므로 `UNKNOWN_TOOL`. (2) prefix가 **등록된 백엔드**를 가리키나 — 아니면 `UNKNOWN_TOOL`. (3) `backend.tools is None`이면(기동 시 죽어 있던 백엔드) 지금 **지연 재집계**(`ensure_session`)를 시도하고, 그래도 안 되면 `BACKEND_UNAVAILABLE`. (4) 그 백엔드에 **실제로 존재하는** tool인지 확인 — 오타·없는 tool은 정책 이전에 걸러낸다.
- **왜?**: 이 네 관문이 전부 정책 평가 **앞에** 놓여 있다. "있는 tool인가"를 끝까지 확인한 뒤에야 정책을 묻는다는 이슈 2의 원칙을 코드로 직접 구현한 것이다. `decision`(audit 라벨)을 `"error"`로 둔 것은 이게 권한 거부가 아니라 잘못된 호출/인프라 문제임을 메트릭에 정확히 남기기 위해서다.

```python
    with observability.tracer().start_as_current_span("policy"):
        decision = policy.evaluate(agent, server, tool, arguments)
    if not decision.allowed:
        fields = {"rule": decision.rule, "agent": agent}
        if decision.detail is not None:
            fields["detail"] = decision.detail
        return error_result("POLICY_DENIED", **fields), "denied"
    with observability.tracer().start_as_current_span("backend_call"):
        result = await backend.call(tool, arguments)
    return result, _relay_decision(result)
```

- **의미**: tool이 확정됐으니 정책을 평가한다. 거부면 `rule`(+가능하면 `detail`)을 담은 `POLICY_DENIED`를 `"denied"` 라벨과 함께 돌려준다. 허용이면 실제 백엔드로 중계하고, 결과의 audit decision은 `_relay_decision`이 정한다.
- **왜?**: 정책 평가와 백엔드 호출을 **별도 span**(`"policy"`, `"backend_call"`)으로 감싸 latency를 분해 관측한다 — `"backend_call"` span의 시간이 "순수 백엔드 호출" 지연이다. 거부 응답에 `rule`을 담는 건 에이전트가 그걸 파싱해 **우회 계획**을 세우게 하기 위해서다(S6 데모 전제).

```python
def _relay_decision(result: types.CallToolResult) -> str:
    if not result.isError:
        return "allowed"
    try:
        code = json.loads(result.content[0].text).get("code")
    except (ValueError, AttributeError, IndexError):
        return "allowed"
    return "error" if code == "BACKEND_UNAVAILABLE" else "allowed"
```

- **의미**: 백엔드에서 중계해 온 결과의 audit decision을 정한다. 에러가 아니면 `allowed`. 에러여도 `code`가 `BACKEND_UNAVAILABLE`일 때만 `error`, 그 외는 `allowed`.
- **왜? (구분의 핵심)**: 정책상 **허용된** 호출이 백엔드까지 갔다면 그 호출은 audit상 `allowed`다 — 설령 백엔드 tool이 자체 에러를 냈더라도(예: 없는 `ticket_id`). 그건 '권한' 문제가 아니라 '실행 결과'이기 때문이다. 오직 Gateway 자신의 인프라 실패(`BACKEND_UNAVAILABLE`)만 `error`로 분류한다 — 그래야 대시보드의 `error` 카운트가 "게이트웨이/백엔드 장애"만 센다.

(메타포: `route_call`은 **경비 데스크의 체크리스트**다. "이 부서가 실존하나 → 이 직원이 실존하나 → 이 사람에게 출입 권한이 있나" 순서로 묻는다. 직원 이름 오타를 "권한 없음"으로 처리하면 안 되듯, 없는 tool은 거부가 아니라 "그런 tool 없음"으로 돌려준다.)

---

## 5. `app.py` — 단일 진입점 조립

### 배경: 왜 이렇게 설계했나

프로젝트 전체의 단일 진입점을 조립하는 곳. 요청 처리 경로는 `call_tool` 핸들러 하나로 유지하고, "인증 → 정책 → 라우팅" 순서를 이 단일 함수 안에 직접 끼워 넣는다(미들웨어 추상화는 일부러 피함). 또 인증 실패 응답이 **이중 계층**인 점이 핵심이다(eng review 이슈 1): `tools/call` 요청은 MCP tool result의 `isError=true`로 거부(S6 파싱 계약), 그 외(`initialize`, `tools/list`)는 HTTP 401. tool result는 `tools/call` 응답에만 존재하므로, 그게 없는 요청은 transport 표준(401)을 따른다.

### 줄별 해설

```python
BACKEND_SPECS = [
    ("ticket", "BACKEND_TICKET_URL", "http://localhost:8101/mcp"),
    ("docs", "BACKEND_DOCS_URL", "http://localhost:8102/mcp"),
    ("ops", "BACKEND_OPS_URL", "http://localhost:8103/mcp"),
]
```

- **의미**: 등록된 백엔드 명세 — `(prefix, env var 이름, 로컬 기본 URL)`. prefix는 tool 네임스페이싱과 라우팅 키를 겸한다.
- **왜?**: URL을 env로 주입 가능하게 둬서 **같은 코드가 로컬(localhost)과 compose(서비스명 DNS) 양쪽에서** 돈다 — Dockerfile/compose가 env로 덮어쓴다.

```python
def build_app() -> FastAPI:
    backends = {
        name: Backend(name, os.environ.get(env, default)) for name, env, default in BACKEND_SPECS
    }
    policy = Policy.load(os.environ.get("GATEWAY_POLICY_PATH", "policies/policy.yaml"))
    audit_path = os.environ.get("GATEWAY_AUDIT_PATH", "audit/audit.jsonl")
    server = Server("agentops-gateway")
```

- **의미**: 앱을 조립하는 **팩토리 함수**. 백엔드 dict(각자 `Backend` 객체), 정책 엔진(기동 시 1회 로드, 핫 리로드 없음), 감사 경로를 env에서 한 번 읽고, MCP 저수준 `Server`를 만든다.
- **왜? (팩토리 함수인 이유)**: 전역으로 앱을 만들지 않고 함수로 감쌌다. 테스트가 `build_app()`을 여러 번 호출해 매번 깨끗한 앱(백엔드/정책/감사 경로가 env로 주입된)을 얻을 수 있어야 하기 때문이다.

```python
    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await aggregate.aggregate_tools(backends)
```

- **의미**: `tools/list` 핸들러. 전 백엔드 tool을 prefix 붙여 집계해 돌려준다.
- **왜?**: 정책으로 필터링하지 **않는다** — support-agent도 ops tool의 '존재'는 봐야 S6 거부 데모가 성립한다(2장과 동일한 의도).

```python
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        arguments = arguments or {}
        request = server.request_context.request
        parts = aggregate.split(name)
        server_name, tool_name = parts if parts else ("unknown", name)
        tracer = observability.tracer()
        with tracer.start_as_current_span("tools/call") as span:
            trace_id = observability.trace_id_hex(span)
            duration_s = None
            try:
                with tracer.start_as_current_span("auth"):
                    agent = auth.authenticate(request.headers.get("authorization"))
            except auth.AuthError as e:
                agent, decision = "anonymous", "auth_failed"
                result = error_result("AUTH_FAILED", reason=e.reason)
            else:
                start = time.perf_counter()
                result, decision = await routes.route_call(backends, policy, agent, name, arguments)
                duration_s = time.perf_counter() - start
            observability.record_call(agent=agent, server=server_name, tool=tool_name,
                                      decision=decision, duration_s=duration_s)
            audit.record(audit_path, agent=agent, tool=name, args=arguments,
                         decision=decision, trace_id=trace_id)
            logger.info("tools/call %s agent=%s decision=%s trace_id=%s", name, agent, decision, trace_id)
            return result
```

- **의미**: **모든 tool 호출이 지나는 단일 처리 경로**다. ① 인증(`auth.authenticate`로 JWT 검증해 `agent_id` 획득) → ② 통과 시 `routes.route_call`로 정책·라우팅, 실패 시 곧장 `AUTH_FAILED` result → ③ 관측(`record_call`) → ④ 감사(`audit.record`). 반환은 항상 `CallToolResult` 하나 — 성공/거부/오류 모두 MCP 표준 result로 표현된다.
- **왜?**: `validate_input=False` — 인자 스키마 검증은 백엔드 몫이고 Gateway는 응답을 그대로 중계한다. 인증 실패 시 `agent`를 `"anonymous"`로 라벨링해 "인증 실패가 있었다"는 사실을 메트릭·감사에 남긴다. `duration_s`는 인증 통과 경로에서만 측정한다 — 인증 실패면 백엔드까지 못 가므로 latency는 `None`. `"tools/call"` 최상위 span 아래 자식 span(`auth`/`policy`/`backend_call`)이 한 `trace_id`를 공유하고, 그 id가 audit·로그에 함께 박혀 **교차 추적**이 된다. 관측과 감사를 **같은 곳에서, 같은 `decision`으로** 기록해 두 시스템이 같은 사실을 보게 한다(eng review 이슈 2).

```python
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        known: dict[str, set[str]] = {}
        for backend in backends.values():
            try:
                await backend.ensure_session()
                known[backend.name] = {t.name for t in backend.tools or []}
            except Exception:
                logger.warning("backend %s unreachable at startup, will retry lazily", backend.name)
        policy.warn_unknown_tools(known)
        async with manager.run():
            yield
        for backend in backends.values():
            await backend.close()
```

- **의미**: 앱 수명 관리. 기동 시 각 백엔드에 미리 붙어(`ensure_session`) tool 목록을 캐시하고, 정책 YAML의 tool 오타를 경고(`warn_unknown_tools`)한 뒤, transport 매니저를 가동하고 `yield`로 요청 처리 구간에 머문다. 종료 시 각 백엔드 세션을 닫는다.
- **왜? (기동 때 미리 붙는 이유)**: 첫 `tools/list`까지 미루지 않고 기동 때 붙으면 (1) 첫 요청 latency가 튀지 않고, (2) 정책 YAML의 tool 오타를 기동 시점에 바로 경고할 수 있다(`warn_unknown_tools`는 집계된 tool 목록이 있어야 동작 — default-deny에선 오타가 '조용한 거부'라 가시화가 중요). 백엔드 하나가 죽어 있어도 **경고만 하고 Gateway는 뜬다**(이후 지연 재집계로 재시도 — 부분 가용성 > 전체 다운). 종료 시 세션을 닫는 것도 **소유 task에서** 깨끗이 처리해 anyio cancel scope 문제를 회피한다.

```python
    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            try:
                auth.authenticate(Headers(scope=scope).get("authorization"))
            except auth.AuthError as e:
                if scope["method"] == "POST":
                    body, receive = await _buffer_body(receive)
                    if _is_tools_call(body):
                        await manager.handle_request(scope, receive, send)
                        return
                response = JSONResponse({"code": "AUTH_FAILED", "reason": e.reason}, status_code=401)
                await response(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)
    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint=MCPEndpoint()))
```

- **의미**: `/mcp`의 바깥 껍데기. transport 매니저에 넘기기 전에 인증을 먼저 본다. 인증 실패라도 **`tools/call`이면 통과**시켜 안쪽 `call_tool` 핸들러가 `isError`(AUTH_FAILED)로 응답하게 하고, 그 외 요청은 HTTP 401로 끊는다.
- **왜? (이중 계층)**: 인증 실패한 `tools/call`을 401로 끊어버리면 거부 사실이 audit/메트릭에 안 남는다. 그래서 통과시켜 안쪽에서 MCP result로 거부하고 audit까지 남긴다. `_buffer_body`가 필요한 이유는 ASGI body가 **한 번만** 흘려보낼 수 있어서다 — "이게 `tools/call`인가?" 보려고 body를 미리 읽으면 정작 매니저에 넘길 body가 사라지므로, 읽은 메시지를 버퍼에 모아 처음부터 재생하는 receive를 만들어 준다.
- **왜? (`Mount`가 아니라 정확 경로 `Route`)**: Starlette `Mount`는 `POST /mcp`를 307로 `/mcp/`로 리다이렉트하는데, 그 리다이렉트가 Streamable HTTP의 세션 핸드셰이크 흐름을 깨뜨린다. FastMCP도 같은 이유로 정확 매칭을 쓴다.

```python
    @app.get("/metrics")
    def metrics() -> Response:
        body, content_type = observability.metrics_payload()
        return Response(body, media_type=content_type)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    admin.register(app, audit_path, os.environ.get("ADMIN_TOKEN"))
    return app
```

- **의미**: 보조 엔드포인트. `/metrics`는 Prometheus 스크레이프용(인증 없음, 내부 네트워크 전제), `/health`는 docker compose healthcheck가 폴링하는 경량 엔드포인트, `admin.register`는 S6 거버넌스 페이지(`ADMIN_TOKEN` 미설정이면 admin 내부에서 전부 403).
- **왜?**: 관측·헬스체크·거버넌스는 tool 호출 경로와 별개의 단순 HTTP 엔드포인트로 분리했다 — 각자 책임이 명확하고 인증 정책도 다르기 때문.

---

## 6. `__main__.py` — 실행 진입점

### 배경: 왜 이렇게 설계했나

`python -m gateway`로 띄우는 진입점. `build_app()`으로 앱을 조립하고 uvicorn으로 서빙하는 단 한 줄이 본질이다.

### 줄별 해설

```python
import uvicorn
from gateway.app import build_app
uvicorn.run(build_app(), host="0.0.0.0", port=8000)
```

- **의미**: 팩토리로 앱을 만들어 `0.0.0.0:8000`에 서빙한다.
- **왜?**: `0.0.0.0` 바인딩은 컨테이너 **안에서 외부(다른 컨테이너/호스트)의 접속을 받기** 위해서다(`127.0.0.1`이면 컨테이너 내부에서만 보임). 포트 8000은 백엔드 포트(8101~8103)와 겹치지 않는 진입점이다.

---

## 7. Live wiring: 실제 호출 지점

이 6개 모듈은 **한 줄기 요청 흐름**으로 엮인다. 에이전트가 `POST /mcp`로 `tools/call`을 보낼 때:

1. **`__main__.py`** → `build_app()`을 띄워 `:8000`에서 대기.
2. **`app.py` `MCPEndpoint`** → 바깥 인증. 실패해도 `tools/call`이면(`_is_tools_call`) `_buffer_body`로 body를 재생 가능하게 만들어 안쪽으로 통과시킨다.
3. **`app.py` `call_tool`** → `auth.authenticate`로 재인증 → 통과 시 `routes.route_call(backends, policy, agent, name, arguments)` 호출.
4. **`routes.py`** → `aggregate.split(name)`으로 prefix 분해 → 백엔드/tool 실존 확인 → `policy.evaluate` → 허용이면 `backend.call(tool, arguments)`.
5. **`upstream.py` `Backend.call`** → `ensure_session`이 준 공유 세션으로 `_race_call`. 실패하면 1회 재연결.
6. 거부·오류가 나는 모든 지점(`app.py`의 AUTH_FAILED, `routes.py`의 UNKNOWN_TOOL/POLICY_DENIED, `upstream.py`의 BACKEND_UNAVAILABLE)은 **전부 `errors.error_result`** 한 함수를 호출 → 동일 봉투 보장.
7. **`app.py`** → 결과의 `decision`을 `observability.record_call`(메트릭)과 `audit.record`(JSONL)에 **같은 값으로** 기록하고 `trace_id`를 박는다.

`tools/list`는 더 짧다: `app.py list_tools` → `aggregate.aggregate_tools(backends)` → 각 `Backend.tools` 캐시(또는 지연 재집계) → prefix 붙여 7개 반환(정책 필터 없음).

외부 결합점은 셋이다: **클라이언트**(에이전트가 `:8000/mcp`로 진입), **백엔드 3종**(`upstream.py`가 env URL로 연결), **관측 스택**(`/metrics`를 Prometheus가 스크레이프, audit JSONL을 admin이 읽음).

---

## 8. 관통하는 설계 원칙 요약

- **단일 경로, 무추상화**: 모든 tool 호출이 `app.py call_tool` 하나를 지난다. 경로가 하나뿐이라 미들웨어 체인을 일부러 안 만들었다 — 추적성 > 유연성(Simplicity First).
- **prefix는 세 역할을 겸한다**: `ticket__`은 (1) 출처 표시, (2) 라우팅 키, (3) 메트릭·audit 라벨. 더블 언더스코어로 충돌을 피하고 첫 구분자에서만 쪼갠다.
- **세션은 풀이 아니라 단일 공유 + 소유 task**: anyio cancel scope의 "진입한 task에서만 탈출" 규칙 때문에, 연결의 enter/exit는 소유 task 안에서만 하고 요청 task들은 세션만 공유한다. 재연결은 백엔드별 `Lock`으로 직렬화, 1회 재시도 후 빠른 실패.
- **오류는 예외가 아니라 result, 형식은 한 함수로만**: `errors.error_result`가 모든 거부·오류 봉투의 단일 진실 지점 — 에이전트가 기계적으로 파싱해 우회 계획을 세우는 S6 계약의 토대다.
- **순서가 의미를 만든다**: 인증 → (tool 실존 확인) → 정책 → 중계. 없는 tool은 `POLICY_DENIED`가 아니라 `UNKNOWN_TOOL`로 — 운영자가 권한 문제로 오해하지 않게.
- **tools/list는 필터링하지 않는다**: 모든 에이전트가 7개 전체를 본다. 거부는 목록이 아니라 호출 시점에 — S6 "보이지만 못 쓰는" 데모를 위한 의도된 설계.
- **부분 가용성 > 전체 다운**: 기동 시 죽은 백엔드는 경고만 하고 건너뛰며, 호출 시점에 지연 재집계로 다시 시도한다. 한 백엔드 장애가 Gateway 전체를 멈추지 않는다.
