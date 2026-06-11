"""구조화 오류 결과 헬퍼 — isError payload 스키마의 단일 진실 지점.

UNKNOWN_TOOL/BACKEND_UNAVAILABLE(S3), POLICY_DENIED/AUTH_FAILED(S4),
RATE_LIMITED(S5)는 전부 이 함수로만 만든다. S6 에이전트가 파싱하는 계약.
"""

import json

import mcp.types as types


def error_result(code: str, **fields: str) -> types.CallToolResult:
    """isError: true + {"code": ..., **fields} JSON 텍스트 단일 블록."""
    payload = {"code": code, **fields}
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=json.dumps(payload))],
    )
