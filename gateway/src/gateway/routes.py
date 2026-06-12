"""tools/call 라우팅 — tool 해석 → 정책 평가 → 백엔드 중계 (인증은 app.py).

평가 순서 고정 (eng review 이슈 2): 정책 평가는 서버·tool이 확정된 후에만 —
미존재 tool은 POLICY_DENIED가 아니라 UNKNOWN_TOOL이다.
반환은 (결과, audit decision) — decision은 allowed|denied|error.
"""

import json

import mcp.types as types

from gateway import aggregate, observability
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.upstream import Backend


def _relay_decision(result: types.CallToolResult) -> str:
    """중계 결과의 audit decision — 게이트웨이 자체 실패(BACKEND_UNAVAILABLE)만 error."""
    if not result.isError:
        return "allowed"
    try:
        code = json.loads(result.content[0].text).get("code")
    except (ValueError, AttributeError, IndexError):
        return "allowed"  # 백엔드 tool 자체 오류의 중계 — 정책상 허용된 호출
    return "error" if code == "BACKEND_UNAVAILABLE" else "allowed"


async def route_call(
    backends: dict[str, Backend], policy: Policy, agent: str, name: str, arguments: dict
) -> tuple[types.CallToolResult, str]:
    parts = aggregate.split(name)
    if parts is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    server, tool = parts
    backend = backends.get(server)
    if backend is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    if backend.tools is None:
        # 등록된 prefix인데 미집계 — 지연 재집계 (기동 시 죽어 있던 백엔드)
        try:
            await backend.ensure_session()
        except Exception:
            return error_result("BACKEND_UNAVAILABLE", server=server), "error"
    if tool not in {t.name for t in backend.tools or []}:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
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
