"""tools/call 라우팅 — prefix를 떼어 백엔드로 전달하고 응답을 그대로 중계."""

import mcp.types as types

from gateway import aggregate
from gateway.errors import error_result
from gateway.upstream import Backend


async def route_call(
    backends: dict[str, Backend], name: str, arguments: dict
) -> types.CallToolResult:
    parts = aggregate.split(name)
    if parts is None:
        return error_result("UNKNOWN_TOOL", tool=name)
    server, tool = parts
    backend = backends.get(server)
    if backend is None:
        return error_result("UNKNOWN_TOOL", tool=name)
    if backend.tools is None:
        # 등록된 prefix인데 미집계 — 지연 재집계 (기동 시 죽어 있던 백엔드)
        try:
            await backend.ensure_session()
        except Exception:
            return error_result("BACKEND_UNAVAILABLE", server=server)
    if tool not in {t.name for t in backend.tools or []}:
        return error_result("UNKNOWN_TOOL", tool=name)
    return await backend.call(tool, arguments)
