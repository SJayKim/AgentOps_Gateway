"""Gateway 코어 AC1-7 — 실제 백엔드 subprocess + 실제 Streamable HTTP 라운드트립.

백엔드 3종은 모듈 단위 subprocess로 기동 (도커 불필요 — CI에서 그대로 돈다).
AC6/AC7은 subprocess kill/재기동으로 장애 시나리오를 실제로 재현한다.
S4부터 무인증 호출이 막히므로 dev-agent(전체 허용) 토큰 픽스처를 쓴다.
"""

import asyncio
import json

from helpers import gateway, mcp_client, payload

EXPECTED_PREFIXED = {
    "ticket__create_ticket",
    "ticket__search_tickets",
    "ticket__update_status",
    "docs__search_docs",
    "docs__read_doc",
    "ops__get_metrics",
    "ops__query_logs",
}


async def test_ac1_tools_list_exactly_seven_prefixed(backends):
    async with gateway() as url, mcp_client(url) as session:
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_PREFIXED


async def test_ac2_create_ticket_matches_direct_backend(backends):
    async with gateway() as url, mcp_client(url) as session:
        via_gw = await session.call_tool(
            "ticket__create_ticket", {"title": "via gateway", "body": "b"}
        )
        assert not via_gw.isError
    async with mcp_client("http://localhost:8101/mcp") as direct_session:
        direct = await direct_session.call_tool("create_ticket", {"title": "direct", "body": "b"})
        assert not direct.isError
    gw_p, d_p = payload(via_gw), payload(direct)
    assert set(gw_p) == set(d_p)  # 동일한 응답 구조
    assert gw_p["status"] == d_p["status"] == "open"


async def test_ac3_one_tool_per_backend(backends):
    async with gateway() as url, mcp_client(url) as session:
        t = await session.call_tool("ticket__search_tickets", {"query": "x"})
        d = await session.call_tool("docs__search_docs", {"query": "deployment"})
        o = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not t.isError and not d.isError and not o.isError


async def test_ac4_unknown_tool_variants(backends):
    async with gateway() as url, mcp_client(url) as session:
        for bad in ("unknown__x", "create_ticket", "ticket__nonexistent"):
            result = await session.call_tool(bad, {})
            assert result.isError, bad
            assert json.loads(result.content[0].text) == {"code": "UNKNOWN_TOOL", "tool": bad}


async def test_ac5_ten_concurrent_calls_shared_session(backends):
    async with gateway() as url, mcp_client(url) as session:

        async def call(i: int):
            result = await session.call_tool(
                "ticket__create_ticket", {"title": f"conc-{i}", "body": "b"}
            )
            assert not result.isError
            return payload(result)["id"]

        ids = await asyncio.gather(*(call(i) for i in range(10)))
        assert len(set(ids)) == 10


async def test_ac6_backend_kill_then_auto_reconnect(backends):
    async with gateway() as url, mcp_client(url) as session:
        ok = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not ok.isError

        backends["ops"].stop()
        down = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert down.isError
        assert json.loads(down.content[0].text) == {"code": "BACKEND_UNAVAILABLE", "server": "ops"}

        backends["ops"].start()
        up = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not up.isError  # 자동 재연결


async def test_ac7_lazy_reaggregation_when_backend_starts_late(backends):
    backends["docs"].stop()
    async with gateway() as url, mcp_client(url) as session:
        # 기동 성공 + 살아있는 백엔드 tool은 정상 동작
        tools = {t.name for t in (await session.list_tools()).tools}
        assert "docs__search_docs" not in tools
        ok = await session.call_tool("ticket__search_tickets", {"query": "x"})
        assert not ok.isError

        backends["docs"].start()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert tools == EXPECTED_PREFIXED  # 지연 재집계로 docs 등장
        doc = await session.call_tool("docs__search_docs", {"query": "deployment"})
        assert not doc.isError
