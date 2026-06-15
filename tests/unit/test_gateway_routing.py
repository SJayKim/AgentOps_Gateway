"""prefix 파싱/조립 + UNKNOWN_TOOL 분기 + errors payload 스키마 (단위)."""

import json

import mcp.types as types

from gateway import aggregate
from gateway.circuit import CircuitBreaker
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.ratelimit import RateLimiter
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


class DownBackend(StubBackend):
    """call이 항상 BACKEND_UNAVAILABLE을 돌려주는 백엔드 — 죽은 백엔드 시뮬레이션."""

    async def call(self, tool, arguments):
        return error_result("BACKEND_UNAVAILABLE", server=self.name)


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


async def test_route_call_rate_limited_before_resolution():
    # 버킷 고갈 시 tool 해석·정책 이전에 RATE_LIMITED(decision=rate_limited)로 즉시 거부
    backends = {"ticket": StubBackend("ticket", ["create_ticket"])}
    rl = RateLimiter(capacity=1, refill_per_sec=0.0)
    ok, decision = await route_call(
        backends, PERMIT_ALL, "test-agent", "ticket__create_ticket", {}, rl
    )
    assert not ok.isError and decision == "allowed"
    limited, decision = await route_call(
        backends, PERMIT_ALL, "test-agent", "ticket__create_ticket", {}, rl
    )
    assert err_payload(limited) == {"code": "RATE_LIMITED", "agent": "test-agent"}
    assert decision == "rate_limited"


async def test_circuit_opens_after_consecutive_backend_failures():
    # 연속 BACKEND_UNAVAILABLE이 threshold에 닿으면 회로 open → 이후 호출은 백엔드를
    # 부르지 않고 즉시 BACKEND_UNAVAILABLE(fail-fast).
    backends = {"ticket": DownBackend("ticket", ["create_ticket"])}
    cb = CircuitBreaker(threshold=2, cooldown_s=30.0)
    name = "ticket__create_ticket"
    for _ in range(2):
        result, decision = await route_call(backends, PERMIT_ALL, "test-agent", name, {}, None, cb)
        assert err_payload(result)["code"] == "BACKEND_UNAVAILABLE"
        assert decision == "error"
    assert cb.is_tripped("ticket") is True
    # open 상태: 백엔드를 부르지 않아야 한다 — call을 폭발시키는 백엔드로 바꿔도 fast-fail.
    backends["ticket"].call = None  # 호출되면 TypeError가 났을 것
    result, decision = await route_call(backends, PERMIT_ALL, "test-agent", name, {}, None, cb)
    assert err_payload(result)["code"] == "BACKEND_UNAVAILABLE"
    assert decision == "error"


async def test_circuit_success_keeps_closed():
    # 정상 호출은 회로를 닫힌 채로 둔다(success 기록).
    backends = {"ticket": StubBackend("ticket", ["create_ticket"])}
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0)
    result, decision = await route_call(
        backends, PERMIT_ALL, "test-agent", "ticket__create_ticket", {}, None, cb
    )
    assert not result.isError and decision == "allowed"
    assert cb.is_tripped("ticket") is False


async def test_aggregate_excludes_tripped_backend():
    # open된 백엔드의 tool은 tools/list 집계에서 빠진다(Epic DoD 8).
    backends = {
        "ticket": StubBackend("ticket", ["create_ticket"]),
        "ops": StubBackend("ops", ["query_logs"]),
    }
    cb = CircuitBreaker(threshold=1, cooldown_s=30.0)
    cb.record_failure("ops")  # ops 회로 open
    names = {t.name for t in await aggregate.aggregate_tools(backends, cb)}
    assert names == {"ticket__create_ticket"}  # ops tool 제외
    # breaker 없으면(None) 제외 없음 — 기존 동작
    names = {t.name for t in await aggregate.aggregate_tools(backends)}
    assert names == {"ticket__create_ticket", "ops__query_logs"}


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
