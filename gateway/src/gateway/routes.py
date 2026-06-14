"""tools/call 라우팅 — tool 해석 → 정책 평가 → 백엔드 중계. (인증은 app.py에서 이미 끝남.)

[평가 순서를 고정한 이유 — eng review 이슈 2]
정책 평가는 server·tool이 '확정된 후에만' 한다. 순서가 중요하다: 존재하지 않는 tool은
POLICY_DENIED가 아니라 UNKNOWN_TOOL로 응답해야 한다. 만약 정책을 먼저 보면, 오타 난
tool 이름이 "정책에 없으니 거부"(POLICY_DENIED)로 잘못 분류돼 운영자가 권한 문제로
오해한다. 그래서 "이 tool이 실제로 있나?"를 먼저 확인하고, 있는 tool에 대해서만 정책을 묻는다.

[반환 계약]
(CallToolResult, audit decision) 튜플. decision은 allowed | denied | error 중 하나로,
app.py가 메트릭·audit에 그대로 기록한다.
"""

import json

import mcp.types as types

from gateway import aggregate, observability
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.upstream import Backend


def _relay_decision(result: types.CallToolResult) -> str:
    """백엔드에서 중계해 온 결과의 audit decision을 정한다.

    [구분의 핵심]
    정책상 '허용된' 호출이 백엔드까지 갔다면, 그 호출은 audit상 allowed다 — 설령 백엔드
    tool이 자체적으로 에러를 냈더라도(예: 존재하지 않는 ticket_id). 그건 '권한' 문제가
    아니라 '실행' 결과이기 때문이다. 오직 Gateway 자신의 인프라 실패(BACKEND_UNAVAILABLE)만
    'error'로 분류한다 — 그래야 대시보드의 error 카운트가 "게이트웨이/백엔드 장애"만 센다.
    """
    if not result.isError:
        return "allowed"
    try:
        code = json.loads(result.content[0].text).get("code")
    except (ValueError, AttributeError, IndexError):
        # 백엔드가 구조화 코드 없이 그냥 에러 텍스트를 준 경우 — tool 자체 오류의 중계로 본다.
        return "allowed"
    return "error" if code == "BACKEND_UNAVAILABLE" else "allowed"


async def route_call(
    backends: dict[str, Backend], policy: Policy, agent: str, name: str, arguments: dict
) -> tuple[types.CallToolResult, str]:
    """prefix 붙은 tool 이름 하나를 받아 해석·정책검사·중계까지 수행한다."""
    # 1) tool 이름을 (server, tool)로 분해. prefix가 없으면 우리가 만든 이름이 아니다 → UNKNOWN_TOOL.
    parts = aggregate.split(name)
    if parts is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    server, tool = parts
    # 2) prefix가 등록된 백엔드를 가리키는지 확인.
    backend = backends.get(server)
    if backend is None:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    if backend.tools is None:
        # 등록된 prefix지만 아직 tool 목록을 못 받음(기동 시 죽어 있던 백엔드) → 지금 재집계 시도.
        try:
            await backend.ensure_session()
        except Exception:
            return error_result("BACKEND_UNAVAILABLE", server=server), "error"
    # 3) 실제로 그 백엔드에 존재하는 tool인지 확인 — 오타·없는 tool은 정책 이전에 걸러낸다(이슈 2).
    if tool not in {t.name for t in backend.tools or []}:
        return error_result("UNKNOWN_TOOL", tool=name), "error"
    # 4) tool이 확정됐으니 이제 정책을 평가한다. trace에 별도 span으로 남겨 latency를 분해 관측.
    with observability.tracer().start_as_current_span("policy"):
        decision = policy.evaluate(agent, server, tool, arguments)
    if not decision.allowed:
        # 거부: rule(+가능하면 detail)을 담아 에이전트가 파싱·우회할 수 있는 POLICY_DENIED로.
        fields = {"rule": decision.rule, "agent": agent}
        if decision.detail is not None:
            fields["detail"] = decision.detail
        return error_result("POLICY_DENIED", **fields), "denied"
    # 5) 허용 → 실제 백엔드로 중계. 이 span의 시간이 "순수 백엔드 호출" latency다.
    with observability.tracer().start_as_current_span("backend_call"):
        result = await backend.call(tool, arguments)
    return result, _relay_decision(result)
