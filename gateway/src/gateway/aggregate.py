"""tool 이름 prefix 네임스페이싱 + tools/list 집계.

[해결하는 문제]
백엔드 3종이 각자 독립적으로 tool 이름을 짓는다(ticket의 search_tickets, docs의
search_docs 등). Gateway가 이들을 하나의 목록으로 합치면 이름 충돌·출처 불명 문제가
생긴다. 그래서 서버 prefix를 붙여 "ticket__search_tickets"처럼 네임스페이싱한다.
이 prefix는 (1) 집계 시 출처 표시, (2) 호출 시 어느 백엔드로 보낼지 결정하는 라우팅
키(routes.py), (3) 메트릭·audit 라벨의 server 차원, 세 역할을 동시에 한다.
"""

import mcp.types as types

# 구분자는 tool 이름에 등장하지 않을 시퀀스로 예약. 언더스코어 2개를 쓰는 이유는
# 일반 tool 이름(snake_case)에 단일 "_"는 흔하지만 "__"는 거의 없어 충돌 위험이 낮기 때문.
SEPARATOR = "__"


def join(server: str, tool: str) -> str:
    """('ticket', 'create_ticket') -> 'ticket__create_ticket'. 집계 시 prefix를 붙인다."""
    return f"{server}{SEPARATOR}{tool}"


def split(name: str) -> tuple[str, str] | None:
    """'ticket__create_ticket' -> ('ticket', 'create_ticket'). prefix가 없으면 None.

    None은 "이 이름은 우리가 prefix를 붙인 게 아니다"라는 신호 — 라우팅에서 UNKNOWN_TOOL로
    이어진다. partition은 첫 SEPARATOR에서만 자르므로 tool 쪽에 "__"가 또 있어도 안전하다.
    """
    if SEPARATOR not in name:
        return None
    server, _, tool = name.partition(SEPARATOR)
    return server, tool


def prefixed(server: str, tools: list[types.Tool]) -> list[types.Tool]:
    """백엔드가 준 tool 목록의 name에 server prefix를 붙인 새 Tool 리스트를 만든다.

    model_copy(update=...)로 이름만 바꾼 복사본을 만든다 — 원본 Tool(스키마·설명 등)은
    그대로 두고 name 필드만 교체해 백엔드 캐시를 오염시키지 않기 위해서다.
    """
    return [tool.model_copy(update={"name": join(server, tool.name)}) for tool in tools]


async def aggregate_tools(backends: dict) -> list[types.Tool]:
    """전 백엔드의 tool 목록을 prefix 붙여 하나로 집계해 반환한다 (tools/list 응답).

    [핵심 설계 — 정책으로 필터링하지 않는다]
    여기서 정책을 적용해 "이 에이전트가 못 쓰는 tool"을 목록에서 빼지 않는다. 모든
    에이전트가 전체 aggregated 목록을 본다. 의도된 데모 설계다: support-agent가 ops tool의
    "존재"를 알아야 S6의 "호출 시도 → 정책 거부 → 우회 계획" 장면이 자연스럽게 발생한다
    (design.md Week 1). 거부는 목록이 아니라 '호출 시점'에 일어난다.
    (production이라면 정책 기반 목록 필터링이 기본값이어야 함 — README에 명시.)

    [지연 재집계]
    기동 때 죽어 있던 백엔드(tools is None)는 이 시점에 다시 붙기를 시도하고, 그래도
    실패하면 조용히 건너뛴다 — 살아있는 백엔드의 tool은 정상 노출하고, 죽은 백엔드는
    다음 tools/list 때 또 시도한다(부분 가용성).
    """
    out: list[types.Tool] = []
    for backend in backends.values():
        if backend.tools is None:  # 아직 한 번도 집계 못 한 백엔드
            try:
                await backend.ensure_session()  # 지금 붙어서 tool 목록 확보 시도
            except Exception:
                continue  # 여전히 죽어 있음 — 이번 목록에선 빼고 다음 기회에 재시도
        out.extend(prefixed(backend.name, backend.tools or []))
    return out
