"""tool 이름 prefix 네임스페이싱 + tools/list 집계."""

import mcp.types as types

SEPARATOR = "__"  # tool 이름에 등장할 수 없는 시퀀스로 예약


def join(server: str, tool: str) -> str:
    return f"{server}{SEPARATOR}{tool}"


def split(name: str) -> tuple[str, str] | None:
    """'ticket__create_ticket' -> ('ticket', 'create_ticket'). prefix 없으면 None."""
    if SEPARATOR not in name:
        return None
    server, _, tool = name.partition(SEPARATOR)
    return server, tool


def prefixed(server: str, tools: list[types.Tool]) -> list[types.Tool]:
    return [tool.model_copy(update={"name": join(server, tool.name)}) for tool in tools]


async def aggregate_tools(backends: dict) -> list[types.Tool]:
    """전 백엔드의 tool 목록을 prefix 붙여 집계.

    정책 필터링 없이 전체를 반환한다 — 의도된 데모 설계: support-agent가
    ops tool의 존재를 알아야 S6의 "호출 시도 → 거부" 장면이 성립한다.
    (production이라면 정책 기반 목록 필터링이 기본값이어야 함 — README 참조)

    미집계 백엔드는 이 시점에 지연 재집계를 시도하고, 실패하면 건너뛴다.
    """
    out: list[types.Tool] = []
    for backend in backends.values():
        if backend.tools is None:
            try:
                await backend.ensure_session()
            except Exception:
                continue  # 여전히 죽어 있음 — 다음 tools/list 때 재시도
        out.extend(prefixed(backend.name, backend.tools or []))
    return out
