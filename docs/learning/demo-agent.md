# 학습 자료: `LangGraph Demo Agent` 완전 해부

> 대상: demo-agent/src/demo_agent/{graph,mcp_client,\_\_main\_\_}.py
> 목적: Gateway가 내린 "정책 거부(POLICY_DENIED)"를 LLM의 변덕에 맡기지 않고 **그래프 구조로** 받아내어 자동으로 우회 계획을 세우는 데모 에이전트를 한 줄씩 이해한다.
> 관련 스펙: docs/specs/06-langgraph-admin.md, docs/design/agentops-gateway-design.md

---

## 0. 큰 그림

```
 [user] "결제 오류 문의 정리 + 서버 로그 원인 분석해줘"
    │
    ▼
 ┌───────────┐  tool_calls 있음?  ┌────────┐
 │ call_model│ ─────── 예 ──────▶ │ tools  │  ← MCP 클라이언트가
 │  (Claude) │ ◀── 거부 아님 ──── │        │     Gateway(:8000) 경유
 └───────────┘                    └───┬────┘     ticket / docs / ops 호출
    │ tool_calls 없음                  │
    ▼                          POLICY_DENIED 잡힘?
  [END]                               │ 예
                                      ▼
                                ┌──────────┐
                                │  bypass  │ rule 파싱 → 우회계획 출력
                                │          │ + docs 대안 실제 수행
                                └────┬─────┘
                                     ▼
                                   [END]
```

이 에이전트가 푸는 문제는 한 문장으로 이렇습니다. **"막다른 길을 만났을 때, 운전자(LLM)가 그냥 '길이 막혔네' 하고 멈추는 게 아니라, 내비게이션 시스템(그래프)이 강제로 우회로 탐색 모드로 전환하게 만들기."** support-agent는 결제 오류를 조사하라는 요청을 받으면 `ticket__search_tickets`(문의 검색)와 `docs__search_docs`(문서 검색)는 권한이 있어 성공하지만, `ops__query_logs`(서버 로그 조회)는 권한 매트릭스상 금지되어 있어 Gateway가 거부합니다.

여기서 핵심 설계 결정이 등장합니다. 만약 "거부되면 우회하라"는 지시를 LLM 프롬프트에 글로 적어두면, LLM은 그날 기분에 따라 거부 응답을 그냥 평범한 "오류"로 뭉개고 "죄송합니다, 로그를 못 봤습니다"로 끝낼 수 있습니다. 신뢰할 수 없죠. 그래서 이 데모는 **거부 감지와 우회 라우팅을 LLM이 아니라 그래프의 분기 함수(`route_after_tools`)로** 옮겼습니다. tool 결과 텍스트가 `{"code":"POLICY_DENIED", ...}` 계약을 만족하면, LLM에게 물어보지 않고 코드가 직접 `bypass` 노드로 라우팅합니다.

비유하자면 이렇습니다. LLM은 "어디로 갈지 말로 떠드는 운전자"이고, LangGraph는 "어떤 조건에서 어떤 차선으로 들어갈지 물리적으로 정해진 도로망"입니다. 운전자가 졸아도 도로가 우회로로 강제 진입시켜 주는 구조 — 그것이 이 데모의 신뢰성 보증입니다(AC3: LLM 호출 없이도 분기 로직을 단위 테스트할 수 있음).

| 파일 | 역할 |
| --- | --- |
| `mcp_client.py` | support-agent JWT를 직접 발급하고, Gateway(:8000)에 MCP 세션을 열고, MCP tool 목록을 Claude가 받는 형식으로 변환 |
| `graph.py` | StateGraph 정의 — 노드(call_model/tools/bypass)·거부 분기·우회 계획. **가장 중요** |
| `__main__.py` | 실행 엔트리포인트 — env에서 키·모델 읽고, 세션·LLM·그래프를 조립해 시나리오를 stdout으로 내레이션 |

---

## 1. `mcp_client.py` — Gateway 연결

이 파일은 "에이전트가 Gateway라는 문 앞에 서서 신분증(JWT)을 보여주고 들어가는" 단계를 담당합니다. 세 개의 작은 함수뿐입니다.

### 1-1. `support_token` — 신분증 직접 발급

```python
def support_token(secret: str, hours: int = 1) -> str:
    """support-agent용 단명 HS256 토큰 — claim은 {agent_id, exp}뿐."""
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    return jwt.encode({"agent_id": "support-agent", "exp": exp}, secret, algorithm="HS256")
```

- **무엇:** 1시간짜리 단명(short-lived) JWT를 만듭니다. 토큰 안에 든 정보(claim)는 딱 두 개 — 내가 누구인지(`agent_id: "support-agent"`)와 언제 만료되는지(`exp`)뿐입니다.
- **왜 직접 발급?** 파일 docstring이 밝히듯 별도의 발급 서버가 없습니다. 데모이므로 `scripts/issue_tokens.py`와 똑같은 claim 구조를 코드 안에서 직접 서명합니다. Gateway는 이 토큰을 받아 `agent_id`를 읽고 "이 에이전트가 이 tool을 쓸 권한이 있나?"를 권한 매트릭스에서 조회합니다.
- **어떻게:** `secret`(공유 비밀키)으로 HS256 대칭 서명. 이 secret은 Gateway와 동일해야 하며, 하드코딩하지 않고 `__main__.py`가 env(`GATEWAY_JWT_SECRET`)에서 읽어 넘깁니다.

### 1-2. `connect` — 세션 여는 문

```python
@asynccontextmanager
async def connect(url: str, token: str):
    """초기화까지 마친 MCP ClientSession을 yield."""
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
```

- **무엇:** Gateway의 `/mcp` 엔드포인트에 HTTP(스트리밍) 연결을 열고, MCP 프로토콜 핸드셰이크(`initialize`)까지 끝낸 `session` 객체를 돌려줍니다.
- **왜 `@asynccontextmanager`?** `async with connect(...) as session:` 형태로 쓰면, 블록을 벗어날 때 연결이 자동으로 정리됩니다. 문을 열었으면 반드시 닫는 것을 언어가 보장 — 연결 누수 방지.
- **어떻게:** 모든 요청 헤더에 `Authorization: Bearer <token>`을 실어 보냅니다. 즉 1-1에서 만든 신분증이 매 호출마다 Gateway에 제시됩니다. Gateway는 이 헤더 하나로 "누가 호출하는가"를 판정합니다.

### 1-3. `to_anthropic_tools` — 통역사

```python
def to_anthropic_tools(mcp_tools) -> list[dict]:
    """MCP Tool 목록 → Claude bind_tools가 받는 {name, description, input_schema} 형식."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in mcp_tools
    ]
```

- **무엇:** Gateway가 알려준 MCP tool 목록(예: `ticket__search_tickets`, `docs__search_docs`, `ops__query_logs`)을 Claude API가 이해하는 tool 정의 형식으로 바꿉니다.
- **왜 필요?** MCP의 tool 객체와 Anthropic의 tool 스키마는 필드 이름이 미묘하게 다릅니다. MCP는 `inputSchema`(카멜케이스), Anthropic은 `input_schema`(스네이크케이스). 두 세계가 같은 tool을 다른 어휘로 부르므로 통역이 필요합니다.
- **어떻게:** 세 필드만 골라 매핑. `description`이 없으면 빈 문자열로 안전 처리. 이 결과를 `llm.bind_tools(...)`에 넘기면 Claude가 "이런 도구들을 쓸 수 있구나" 하고 인식합니다.

---

## 2. `graph.py` — LangGraph 그래프·노드·거부 분기 (가장 중요)

### 배경: 왜 거부를 프롬프트가 아닌 그래프 노드로 처리하나

파일 맨 위 docstring이 설계 의도를 직접 못 박습니다.

```python
"""support-agent를 StateGraph로 구현 — 거부 분기를 LLM이 아닌 그래프 구조로 보장.

핵심 계약 (spec): tools 노드가 POLICY_DENIED를 감지하면 state["denial"]에 payload를
박고, route_after_tools가 그것을 보고 bypass 노드로 라우팅한다. LLM은 이 분기 결정에
관여하지 않는다 — "거부를 오류로 뭉개는" LLM 실패 모드를 구조로 차단 (AC3: LLM 호출
없이 단위 테스트 가능).
"""
```

스펙(06-langgraph-admin.md)의 데모 시나리오는 ④단계에서 **에이전트가 거부 payload를 파싱하고 우회 계획을 출력**할 것을 요구합니다. 만약 이 로직을 "거부당하면 우회하세요"라고 프롬프트에 적으면, 그것은 LLM에 대한 *요청*일 뿐 *보증*이 아닙니다. LLM은 확률적이라 거부를 일반 오류와 혼동해 "실패했습니다"로 끝낼 수 있습니다.

그래서 거부 처리를 **결정론적 코드 경로**로 끌어냈습니다. tool 결과가 거부 계약을 만족하면 분기 함수가 무조건 `bypass` 노드로 보냅니다. 부수 효과로, 이 분기 로직(`extract_denial`, `route_after_tools`, `format_bypass_plan`)은 LLM이나 네트워크 없이 순수 함수로 테스트할 수 있습니다 — 이것이 docstring이 말하는 AC3입니다.

### 2-1. State 정의 — 그래프가 들고 다니는 가방

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    denial: dict | None  # POLICY_DENIED payload — tools 노드가 설정
    bypass_done: bool  # 우회 1회만 — 무한 루프 방지
```

- `messages`: 대화 이력. `add_messages`는 노드가 반환한 메시지를 덮어쓰지 않고 **누적**하라는 리듀서(reducer)입니다 — 대화가 점점 쌓이는 게 정상이므로.
- `denial`: tools 노드가 거부를 잡으면 여기에 payload를 넣습니다. 노드 간 "신호 깃발". 비유하면, 한 노드가 다음 노드에게 건네는 메모지입니다.
- `bypass_done`: 우회를 한 번만 하기 위한 플래그. 이게 없으면 거부 → 우회 → 다시 모델 → 또 거부 → 무한 반복이 될 수 있습니다.

### 2-2. `extract_denial` — 거부인지 일반 오류인지 가려내는 체

```python
def extract_denial(text: str) -> dict | None:
    """tool 결과 텍스트가 POLICY_DENIED payload면 그 dict, 아니면 None.

    generic 백엔드 오류(BACKEND_UNAVAILABLE 등)는 None — 거부만 우회로 보낸다.
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(payload, dict) and payload.get("code") == "POLICY_DENIED":
        return payload
    return None
```

- **무엇:** tool이 돌려준 텍스트를 JSON으로 파싱해, `code == "POLICY_DENIED"`인 경우에만 그 dict를 반환합니다.
- **왜 이렇게 좁게?** docstring이 명시하듯, 거부(POLICY_DENIED)만 우회로 보내야 합니다. 백엔드가 잠깐 죽은 일반 장애(`BACKEND_UNAVAILABLE` 등)는 우회 대상이 아니라 그냥 오류이므로 `None`을 반환해 평소 흐름으로 흘려보냅니다.
- **어떻게:** JSON이 아니거나(평범한 성공 텍스트) dict가 아니면 조용히 `None`. 즉 **거부라는 특수 신호만 정확히 골라내는 체** 역할입니다. 이 함수가 소비하는 계약은 S4가 정의한 `{"code":"POLICY_DENIED","rule":"<agent>:<server>:<tool>","agent":...}` 입니다.

### 2-3. 라우팅 함수 두 개 — 도로의 분기점

```python
def route_after_model(state: State) -> str:
    """마지막 AI 메시지에 tool_call이 있으면 tools, 없으면 종료."""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def route_after_tools(state: State) -> str:
    """거부가 잡혔고 아직 우회 안 했으면 bypass, 아니면 다시 모델로 — LLM 무관."""
    if state.get("denial") and not state.get("bypass_done"):
        return "bypass"
    return "call_model"
```

- `route_after_model`: 모델이 도구를 호출하려 하면(`tool_calls`가 있으면) `tools` 노드로, 아니면 끝(`END`). "운전자가 도구를 쓰겠다고 했나?"를 보는 분기.
- `route_after_tools`: **이 데모의 심장.** `denial`이 있고 아직 우회 전이면 `bypass`로 강제 진입, 아니면 `call_model`로 돌아가 LLM이 결과를 보고 다음 행동을 정합니다. 주석 끝의 "LLM 무관"이 핵심 — 이 갈림길 판정에 LLM은 단 한 톨도 개입하지 않습니다.

### 2-4. `format_bypass_plan` — rule을 인용해 "파싱했음"을 증명

```python
def format_bypass_plan(denial: dict) -> str:
    """거부 payload의 rule을 참조하는 우회 계획 — 파싱 증명 (AC2)."""
    rule = denial.get("rule", "<unknown>")
    return (
        f"ops 로그 접근이 정책으로 거부되었습니다 (rule: {rule}). "
        "대안: ① dev-agent에 로그 조회 위임 ② 문서의 알려진 오류 패턴으로 1차 분석 "
        "③ 권한 승인 요청. 지금은 ②를 수행합니다."
    )
```

- **무엇:** 거부 payload에서 `rule` 필드(예: `support-agent:ops:query_logs`)를 뽑아 우회 계획 문장에 그대로 박습니다.
- **왜 rule을 인용?** 단순히 "거부됨, 우회함"이 아니라 *어떤 규칙*에 막혔는지 문구에 명시함으로써, 에이전트가 거부 payload를 실제로 **읽고 이해했다**는 증거를 남깁니다(스펙 AC2). 막연한 사과가 아닌, 막힌 규칙을 짚는 구체적 대응.
- **어떻게:** 세 가지 대안(위임/문서분석/승인요청)을 제시한 뒤 "②를 수행합니다"로 다음 행동을 예고합니다.

### 2-5. `build_graph` — 부품을 도로망으로 조립

```python
def build_graph(session, llm, tool_defs: list[dict]):
    """session(MCP)·llm(Claude)·tool_defs(Anthropic 형식)를 묶어 컴파일된 그래프 반환."""
    model = llm.bind_tools(tool_defs)
```

- 세 의존성을 주입받습니다 — MCP `session`(Gateway 연결), `llm`(Claude), `tool_defs`(1-3에서 변환한 도구 정의). `bind_tools`로 모델에 도구를 장착합니다.

**call_model 노드 — 운전자에게 묻기**

```python
    async def call_model(state: State) -> dict:
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}
```

대화 이력을 통째로 Claude에 보내 다음 발화(도구 호출 또는 최종 답변)를 받습니다. 이것이 데모에서 **유일한 실 LLM API 호출 지점**입니다.

**tools 노드 — 도구를 Gateway 경유로 실제 실행**

```python
    async def tools(state: State) -> dict:
        last = state["messages"][-1]
        out: list[AnyMessage] = []
        denial = None
        for call in last.tool_calls:
            result = await session.call_tool(call["name"], call["args"])
            text = _result_text(result)
            out.append(ToolMessage(content=text, tool_call_id=call["id"], name=call["name"]))
            denial = denial or extract_denial(text)
        return {"messages": out, "denial": denial}
```

- 모델이 요청한 도구들을 하나씩 `session.call_tool`로 Gateway에 보냅니다. **모든 호출은 Gateway를 통과**하므로 권한 검사·audit이 여기서 일어납니다.
- 각 결과는 `ToolMessage`로 대화에 누적되고, 동시에 `extract_denial`로 거부 여부를 검사합니다. `denial = denial or extract_denial(text)` — 한 배치에서 **하나라도** 거부가 있으면 그 깃발을 들고 나옵니다(첫 거부를 보존).
- 반환된 `denial`이 State에 박히고, 곧바로 `route_after_tools`가 이를 보고 분기합니다.

**bypass 노드 — 우회 계획 + 대안 실제 수행**

```python
    async def bypass(state: State) -> dict:
        plan = format_bypass_plan(state["denial"])
        alt = await session.call_tool(ALT_DOCS_TOOL, {"query": ALT_DOCS_QUERY})
        report = (
            f"{plan}\n\n[대안 ② 실행] {ALT_DOCS_TOOL}('{ALT_DOCS_QUERY}') 결과: "
            f"{_result_text(alt)[:400]}"
        )
        return {"messages": [AIMessage(content=report)], "bypass_done": True}
```

- 막혔을 때 단지 계획만 말하는 게 아니라, **실제로** 대안 도구(`docs__search_docs`, 쿼리 `"payment error"` — 파일 상단 상수)를 호출해 결과를 가져옵니다. 말과 행동이 일치하는 우회.
- `bypass_done: True`를 세워 우회가 두 번 일어나지 않게 잠급니다.

상수 정의(파일 21–22행):
```python
ALT_DOCS_TOOL = "docs__search_docs"  # 우회 대안: 문서의 알려진 오류 패턴 1차 분석
ALT_DOCS_QUERY = "payment error"
```

**그래프 배선 — 도로를 깔다**

```python
    g = StateGraph(State)
    g.add_node("call_model", call_model)
    g.add_node("tools", tools)
    g.add_node("bypass", bypass)
    g.add_edge(START, "call_model")
    g.add_conditional_edges("call_model", route_after_model, {"tools": "tools", END: END})
    g.add_conditional_edges(
        "tools", route_after_tools, {"bypass": "bypass", "call_model": "call_model"}
    )
    g.add_edge("bypass", END)
    return g.compile()
```

- `START → call_model`: 항상 모델로 시작.
- `call_model`에서 조건부 분기: 도구 호출이 있으면 `tools`, 없으면 `END`.
- `tools`에서 조건부 분기: 거부면 `bypass`, 아니면 다시 `call_model`(결과를 보고 이어가기).
- `bypass → END`: 우회를 마치면 종료.
- `compile()`로 실행 가능한 그래프 객체를 돌려줍니다. 이 배선표가 곧 0장 ASCII 다이어그램의 실체입니다.

보조 함수(68–72행)는 MCP 결과에서 안전하게 텍스트를 꺼내는 헬퍼입니다:
```python
def _result_text(result) -> str:
    try:
        return result.content[0].text
    except (AttributeError, IndexError):
        return ""
```

---

## 3. `__main__.py` — 실행 엔트리·내레이션

이 파일은 "무대를 차리고 막을 올리는" 진행자입니다. 부품(세션·LLM·그래프)을 조립하고 시나리오를 사람이 읽을 수 있게 stdout으로 풀어냅니다.

### 3-1. 상단 docstring — 실행법과 env 계약

```python
"""support-agent 데모 실행 — 시나리오를 stdout으로 내레이션 (수동 데모, CI 제외).

실행:
    GATEWAY_JWT_SECRET=<secret> ANTHROPIC_API_KEY=<key> uv run python -m demo_agent

env:
    GATEWAY_URL        기본 http://localhost:8000/mcp
    DEMO_AGENT_MODEL   기본 claude-sonnet-4-6 (코드 하드코딩 금지 — env로만)
"""
```

- 실 LLM API 키가 필요하므로 **CI에서 제외**, compose에도 안 넣고 수동 실행합니다. 모델 ID와 키는 절대 하드코딩하지 않고 **env로만** 주입합니다.

### 3-2. 사용자 요청 + 내레이션 헬퍼

```python
USER_REQUEST = "최근 결제 오류 관련 고객 문의를 정리하고, 서버 로그에서 원인을 찾아줘"


def _narrate(message) -> None:
    role = type(message).__name__.replace("Message", "").lower()
    if getattr(message, "tool_calls", None):
        for call in message.tool_calls:
            print(f"[{role}] → tool_call {call['name']}({call['args']})")
    text = message.content if isinstance(message.content, str) else str(message.content)
    if text.strip():
        print(f"[{role}] {text}")
```

- `USER_REQUEST`가 시나리오의 방아쇠입니다. "문의 정리"(ticket/docs 허용)와 "서버 로그 원인"(ops 거부)을 한 문장에 담아, 의도적으로 거부를 유발합니다.
- `_narrate`는 각 메시지를 `[human]`, `[ai]`, `[tool]` 같은 라벨과 함께 출력합니다. 도구 호출이 있으면 `→ tool_call 이름(인자)` 형태로 보여주어, 데모를 보는 사람이 흐름을 눈으로 따라갈 수 있게 합니다.

### 3-3. `main` — 조립과 실행

```python
async def main() -> None:
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    model = os.environ.get("DEMO_AGENT_MODEL", "claude-sonnet-4-6")
    token = support_token(os.environ["GATEWAY_JWT_SECRET"])
    llm = ChatAnthropic(model=model, api_key=os.environ["ANTHROPIC_API_KEY"])

    async with connect(url, token) as session:
        tool_defs = to_anthropic_tools((await session.list_tools()).tools)
        graph = build_graph(session, llm, tool_defs)
        print(f"[demo] support-agent 연결됨 ({url}) · model={model}")
        print(f"[user] {USER_REQUEST}\n")
        final = await graph.ainvoke(
            {"messages": [HumanMessage(USER_REQUEST)], "denial": None, "bypass_done": False},
            config={"recursion_limit": 25},
        )
        for message in final["messages"]:
            _narrate(message)
```

- **준비:** URL·모델은 env에서(기본값 포함), 토큰은 `support_token`으로 발급, LLM은 `ChatAnthropic`으로 생성 — 키는 `os.environ["ANTHROPIC_API_KEY"]`. env 키가 없으면 즉시 KeyError로 실패해 "키 빠뜨림"을 조용히 넘기지 않습니다.
- **조립:** `connect`로 세션을 열고 → `list_tools`로 Gateway가 노출한 도구 목록을 받아 → `to_anthropic_tools`로 변환 → `build_graph`로 그래프 컴파일.
- **실행:** 초기 State는 `denial: None`, `bypass_done: False`로 시작. `ainvoke`로 그래프를 한 번 돌리면 내부적으로 call_model → tools → (거부 시) bypass 흐름이 자동 진행됩니다. `recursion_limit: 25`는 모델↔도구 왕복이 폭주하지 않게 거는 안전핀.
- **출력:** 끝난 뒤 누적된 모든 메시지를 `_narrate`로 풀어 시나리오 전체를 내레이션합니다.

마지막 줄은 표준 엔트리포인트입니다:
```python
if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Live wiring: demo-agent가 Gateway의 POLICY_DENIED 계약을 소비하고 흔적을 남기는 법

이 데모는 다른 컴포넌트들이 만든 계약 위에서 동작합니다 — 자기 파일만으로는 완성되지 않고, **Gateway와의 약속**으로 완성됩니다.

1. **신분 제시:** `mcp_client.support_token`이 `agent_id: "support-agent"` JWT를 발급하고, `connect`가 매 요청 `Authorization: Bearer` 헤더로 Gateway에 보냅니다. Gateway는 이 한 줄로 "누가 호출했는가"를 정합니다.
2. **권한 강제:** support-agent가 `ops__query_logs`를 호출하면, Gateway는 권한 매트릭스(3 에이전트 × 3 서버)를 tool call 단위로 조회해 거부합니다. 그 응답이 곧 S4가 정의한 계약 — `{"code":"POLICY_DENIED","rule":"support-agent:ops:query_logs","agent":...}`.
3. **계약 소비:** `graph.extract_denial`이 이 payload를 정확히 `code == "POLICY_DENIED"` 기준으로 식별하고, `format_bypass_plan`이 `rule` 필드를 우회 문구에 인용합니다. 즉 **에이전트 쪽 코드는 Gateway가 약속한 JSON 모양에 직접 의존**합니다 — 그래서 이 계약은 양쪽이 함께 지켜야 하는 인터페이스입니다.
4. **audit/admin 흔적:** 모든 `session.call_tool`이 Gateway를 통과하므로, 허용된 ticket/docs 호출도, 거부된 ops 호출도 Gateway의 audit 로그에 기록됩니다. 데모를 한 번 돌리면 admin 페이지에서 "support-agent가 ops:query_logs로 POLICY_DENIED를 받은" 기록을 실제로 확인할 수 있습니다. demo-agent는 audit에 직접 쓰지 않습니다 — 그저 Gateway를 경유하기만 하면 관측 흔적이 자동으로 남습니다(관측은 Gateway의 책임, 에이전트는 무지).

설계 근거: 거부 계약의 형태와 audit 기록 책임은 docs/design/agentops-gateway-design.md와 docs/specs/06-langgraph-admin.md에 정의되어 있으며, 이 데모는 그 계약의 **소비자**입니다.

---

## 5. 관통하는 설계 원칙 요약

- **거부 = 구조적 분기, LLM 무관.** POLICY_DENIED 감지와 우회 라우팅을 `route_after_tools`라는 결정론적 함수에 둠으로써, LLM이 거부를 "일반 오류"로 뭉개는 실패 모드를 코드 구조로 원천 차단한다(AC3).
- **거부만 우회, 일반 장애는 통과.** `extract_denial`은 `code == "POLICY_DENIED"`만 잡고 `BACKEND_UNAVAILABLE` 같은 장애는 `None`으로 흘려보낸다 — 우회 트리거를 좁게 정의.
- **파싱 증명을 출력으로 남긴다.** `format_bypass_plan`이 거부 payload의 `rule`을 우회 문구에 인용해, 막연한 사과가 아니라 "막힌 규칙을 읽고 대응했다"는 증거를 남긴다(AC2).
- **말이 아닌 행동으로 우회.** bypass 노드는 계획만 출력하지 않고 실제 대안 도구(`docs__search_docs`)를 호출해 결과를 가져온다.
- **env로만 키·모델 주입, 하드코딩 금지.** 모델 ID(`claude-sonnet-4-6` 기본)·API 키·Gateway secret 모두 `os.environ`에서만 읽어, 비밀이 코드에 박히지 않는다.
- **독립 패키지로 격리.** demo-agent는 langgraph·langchain-anthropic·mcp에 의존하는 별도 패키지로, Gateway 의존성에 LLM 라이브러리를 섞지 않는다. compose에도 넣지 않고 수동 실행(키 필요·CI 제외).
- **모든 호출은 Gateway 경유.** 에이전트는 백엔드 MCP 서버를 직접 호출하지 않고 단일 진입점(:8000)만 안다 — 인증·정책·audit이 한 곳에서 강제되고, 에이전트는 관측에 무지한 채로도 흔적을 남긴다.
