"""prefix 파싱/조립 + UNKNOWN_TOOL 분기 + errors payload 스키마 (단위)."""

import json

import mcp.types as types

from gateway import aggregate
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.routes import route_call


def err_payload(result):
    assert result.isError
    return json.loads(result.content[0].text)


class StubBackend:
    """집계 완료 상태의 백엔드 — call은 성공 결과를 그대로 돌려준다."""

    def __init__(self, name, tool_names):
        self.name = name
        self.tools = [
            types.Tool(name=t, inputSchema={"type": "object", "properties": {}}) for t in tool_names
        ]

    async def call(self, tool, arguments):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({"tool": tool}))]
        )


def test_prefix_join_split_roundtrip():
    assert aggregate.join("ticket", "create_ticket") == "ticket__create_ticket"
    assert aggregate.split("ticket__create_ticket") == ("ticket", "create_ticket")
    assert aggregate.split("create_ticket") is None
    # 첫 구분자 기준 분리 — tool 쪽에 __가 남아도 안전
    assert aggregate.split("a__b__c") == ("a", "b__c")


def test_prefixed_tools_keep_schema():
    tools = [types.Tool(name="t1", inputSchema={"type": "object", "properties": {"x": {}}})]
    out = aggregate.prefixed("docs", tools)
    assert out[0].name == "docs__t1"
    assert out[0].inputSchema == tools[0].inputSchema
    assert tools[0].name == "t1"  # 원본 불변


PERMIT_ALL = Policy({"test-agent": {"ticket": ["create_ticket"]}})


async def test_route_call_unknown_tool_branches():
    backends = {"ticket": StubBackend("ticket", ["create_ticket"])}
    for bad in ("create_ticket", "unknown__x", "ticket__nonexistent"):
        result, decision = await route_call(backends, PERMIT_ALL, "test-agent", bad, {})
        assert err_payload(result) == {"code": "UNKNOWN_TOOL", "tool": bad}, bad
        assert decision == "error"
    ok, decision = await route_call(backends, PERMIT_ALL, "test-agent", "ticket__create_ticket", {})
    assert not ok.isError
    assert decision == "allowed"


async def test_route_call_resolves_tool_before_policy():
    # 미존재 tool은 POLICY_DENIED가 아니라 UNKNOWN_TOOL — 평가 순서 고정 계약
    backends = {"ticket": StubBackend("ticket", ["create_ticket"])}
    result, decision = await route_call(
        backends, PERMIT_ALL, "rogue-agent", "ticket__nonexistent", {}
    )
    assert err_payload(result)["code"] == "UNKNOWN_TOOL"
    result, decision = await route_call(
        backends, PERMIT_ALL, "rogue-agent", "ticket__create_ticket", {}
    )
    assert err_payload(result) == {
        "code": "POLICY_DENIED",
        "rule": "rogue-agent:ticket:create_ticket",
        "agent": "rogue-agent",
    }
    assert decision == "denied"


def test_error_result_payload_schema():
    # code별 필드 정확성 — S6가 파싱하는 계약, 여기서 1회 고정
    cases = {
        ("UNKNOWN_TOOL",): {"tool": "x__y"},
        ("BACKEND_UNAVAILABLE",): {"server": "ops"},
        ("POLICY_DENIED",): {"rule": "support-agent:ops:query_logs", "agent": "support-agent"},
    }
    for (code,), fields in cases.items():
        result = error_result(code, **fields)
        assert result.isError
        assert err_payload(result) == {"code": code, **fields}
