"""거부 분기 노드 라우팅 + payload 파싱 (AC3) — LLM·MCP 호출 없이 순수 함수만.

POLICY_DENIED 모의 응답을 state에 주입했을 때 route_after_tools가 bypass로 가는지,
generic 오류는 가지 않는지, 우회 계획이 rule을 참조하는지를 검증한다.
"""

from demo_agent import graph

DENIAL = {"code": "POLICY_DENIED", "rule": "support-agent:ops:query_logs", "agent": "support-agent"}


# --- extract_denial: 거부만 감지, generic 오류는 무시 ---


def test_extract_denial_recognizes_policy_denied():
    assert graph.extract_denial('{"code": "POLICY_DENIED", "rule": "r"}') == {
        "code": "POLICY_DENIED",
        "rule": "r",
    }


def test_extract_denial_ignores_other_errors_and_junk():
    assert graph.extract_denial('{"code": "BACKEND_UNAVAILABLE"}') is None
    assert graph.extract_denial("not json at all") is None
    assert graph.extract_denial('{"ok": true}') is None


# --- route_after_tools: 거부 → bypass, 그 외 → call_model (LLM 무관) ---


def test_route_to_bypass_on_denial():
    state = {"messages": [], "denial": DENIAL, "bypass_done": False}
    assert graph.route_after_tools(state) == "bypass"


def test_route_back_to_model_without_denial():
    state = {"messages": [], "denial": None, "bypass_done": False}
    assert graph.route_after_tools(state) == "call_model"


def test_route_does_not_loop_after_bypass():
    state = {"messages": [], "denial": DENIAL, "bypass_done": True}
    assert graph.route_after_tools(state) == "call_model"  # 우회 1회만


# --- format_bypass_plan: 거부 payload의 rule 참조 (파싱 증명, AC2) ---


def test_bypass_plan_references_rule():
    plan = graph.format_bypass_plan(DENIAL)
    assert "support-agent:ops:query_logs" in plan
