"""구조화 오류 결과 헬퍼 — isError payload 스키마의 "단일 진실 지점(single source of truth)".

[이 모듈이 존재하는 이유]
Gateway가 내는 모든 거부·오류 응답은 에이전트가 기계적으로 파싱할 수 있어야 한다
(design.md "거부 응답 형식": S6 LangGraph 에이전트가 거부를 받고 우회 계획을 세우는
데모의 전제). 그래서 오류 payload 형식을 코드 곳곳에 흩어 두지 않고 이 함수 하나로만
생성한다. 형식이 한 곳에만 있으면 계약이 깨질 일이 없다.

[이 함수로 만들어지는 오류 코드들 — 스프린트별]
  - UNKNOWN_TOOL / BACKEND_UNAVAILABLE : S3(라우팅·중계)
  - POLICY_DENIED / AUTH_FAILED        : S4(인증·정책)
  - RATE_LIMITED                       : S5(관측·레이트리밋, stretch)
모두 같은 봉투({"code": ..., ...추가 필드})에 담겨 나간다.
"""

import json

import mcp.types as types


def error_result(code: str, **fields: str) -> types.CallToolResult:
    """isError=true 인 MCP tool result를 만든다. 본문은 {"code": code, **fields} JSON 한 블록.

    [왜 예외가 아니라 result인가]
    MCP에서 "tool이 실패했다"는 정상적인 응답 형태(isError=true result)다. 거부·오류를
    HTTP 에러나 파이썬 예외로 던지면 transport 계층에서 끊겨 에이전트가 구조화된 사유를
    못 받는다. result로 돌려줘야 content[0].text의 JSON을 파싱해 code/rule/reason을
    읽고 다음 행동을 결정할 수 있다.

    code 외 추가 필드는 호출처가 **fields로 자유롭게 붙인다 (예: POLICY_DENIED엔 rule·agent,
    AUTH_FAILED엔 reason). 텍스트 콘텐츠 단일 블록으로 직렬화한다.
    """
    payload = {"code": code, **fields}
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=json.dumps(payload))],
    )
