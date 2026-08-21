"""T4 — GATEWAY_MCP_STATELESS 토글. 켠 채로 e2e가 도는지 실측한다.

[왜 이 테스트가 먼저인가]
설계(결정 1C)는 두 세션 전략을 비교하겠다고만 했지, stateless 모드에서
streamablehttp_client 핸드셰이크가 도는지는 검증한 적이 없다. 안 돌면 1C의 A 경로가
통째로 사라진다. 여기서 실측해 그 가정을 고정한다.

[왜 session id로 단언하나]
e2e 성공만 보면 토글을 무시하고 항상 stateful로 둬도 통과한다 — 테스트가 토글을 검증하지
못한다. stateless 모드의 관측 가능한 차이는 서버가 mcp-session-id를 발급하지 않는다는 것
이고, streamablehttp_client가 그 값을 get_session_id()로 그대로 노출한다.
"""

from helpers import auth_headers, gateway, payload
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TOOLS = {
    "ticket__create_ticket",
    "ticket__search_tickets",
    "ticket__update_status",
    "docs__search_docs",
    "docs__read_doc",
    "ops__get_metrics",
    "ops__query_logs",
}


async def test_session_id_issued_when_toggle_off(backends):
    """기본값(토글 미설정)은 stateful — 서버가 세션 id를 발급한다. 아래 on 케이스의 대조군."""
    async with gateway() as url:
        async with streamablehttp_client(url, headers=auth_headers("dev-agent")) as (
            read,
            write,
            get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert get_session_id() is not None


async def test_stateless_serves_full_e2e_without_session_id(backends, monkeypatch):
    """토글 on: 세션 id 없이 initialize → tools/list → tools/call이 끝까지 돈다.

    tools/list가 백엔드 3종 집계를 요구하고 tools/call이 인증·정책·라우팅을 전부 거치므로,
    이 한 케이스가 stateless에서 게이트웨이 경로 전체를 밟는다.
    """
    monkeypatch.setenv("GATEWAY_MCP_STATELESS", "1")
    async with gateway() as url:
        async with streamablehttp_client(url, headers=auth_headers("dev-agent")) as (
            read,
            write,
            get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert get_session_id() is None  # 토글이 실제로 먹었다는 증거

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == TOOLS

                result = await session.call_tool("docs__search_docs", {"query": "deployment"})
                assert not result.isError
                assert payload(result)
