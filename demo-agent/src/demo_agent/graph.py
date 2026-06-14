"""support-agent를 StateGraph로 구현 — 거부 분기를 LLM이 아닌 그래프 구조로 보장.

핵심 계약 (spec): tools 노드가 POLICY_DENIED를 감지하면 state["denial"]에 payload를
박고, route_after_tools가 그것을 보고 bypass 노드로 라우팅한다. LLM은 이 분기 결정에
관여하지 않는다 — "거부를 오류로 뭉개는" LLM 실패 모드를 구조로 차단 (AC3: LLM 호출
없이 단위 테스트 가능).

그래프:
    START → call_model ─(tool_calls?)─→ tools ─(denial?)─→ bypass → END
                  ↑                        │
                  └────(no denial)─────────┘
"""

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

ALT_DOCS_TOOL = "docs__search_docs"  # 우회 대안: 문서의 알려진 오류 패턴 1차 분석
ALT_DOCS_QUERY = "payment error"


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    denial: dict | None  # POLICY_DENIED payload — tools 노드가 설정
    bypass_done: bool  # 우회 1회만 — 무한 루프 방지


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


def route_after_model(state: State) -> str:
    """마지막 AI 메시지에 tool_call이 있으면 tools, 없으면 종료."""
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def route_after_tools(state: State) -> str:
    """거부가 잡혔고 아직 우회 안 했으면 bypass, 아니면 다시 모델로 — LLM 무관."""
    if state.get("denial") and not state.get("bypass_done"):
        return "bypass"
    return "call_model"


def format_bypass_plan(denial: dict) -> str:
    """거부 payload의 rule을 참조하는 우회 계획 — 파싱 증명 (AC2)."""
    rule = denial.get("rule", "<unknown>")
    return (
        f"ops 로그 접근이 정책으로 거부되었습니다 (rule: {rule}). "
        "대안: ① dev-agent에 로그 조회 위임 ② 문서의 알려진 오류 패턴으로 1차 분석 "
        "③ 권한 승인 요청. 지금은 ②를 수행합니다."
    )


def _result_text(result) -> str:
    try:
        return result.content[0].text
    except (AttributeError, IndexError):
        return ""


def build_graph(session, llm, tool_defs: list[dict]):
    """session(MCP)·llm(Claude)·tool_defs(Anthropic 형식)를 묶어 컴파일된 그래프 반환."""
    model = llm.bind_tools(tool_defs)

    async def call_model(state: State) -> dict:
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

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

    async def bypass(state: State) -> dict:
        plan = format_bypass_plan(state["denial"])
        alt = await session.call_tool(ALT_DOCS_TOOL, {"query": ALT_DOCS_QUERY})
        report = (
            f"{plan}\n\n[대안 ② 실행] {ALT_DOCS_TOOL}('{ALT_DOCS_QUERY}') 결과: "
            f"{_result_text(alt)[:400]}"
        )
        return {"messages": [AIMessage(content=report)], "bypass_done": True}

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
