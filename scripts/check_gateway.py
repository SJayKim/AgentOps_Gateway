"""compose 기동 상태에서 Gateway(:8000) 경유 AC1-3 수동 확인 (check_servers.py의 짝).

check_servers가 백엔드에 직접 붙었다면, 이 스크립트는 Gateway를 통해서 본다 — 그래서
tool 이름이 prefix가 붙은 형태(ticket__create_ticket 등)로 나와야 하고, 그게 곧 집계·
라우팅이 동작한다는 증거다. 인증 없이 호출하는데, 이 스크립트는 정책 거부가 아니라
'집계·중계 경로'를 확인하는 용도라 토큰 시나리오는 e2e_demo.py가 따로 맡는다.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Gateway가 7개 tool을 prefix 붙여 노출해야 한다(3 백엔드의 tool 합집합). 이 집합과
# 정확히 일치하는지로 AC1(집계)을 단언한다.
EXPECTED = {
    "ticket__create_ticket",
    "ticket__search_tickets",
    "ticket__update_status",
    "docs__search_docs",
    "docs__read_doc",
    "ops__get_metrics",
    "ops__query_logs",
}


async def main() -> None:
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            # AC1: 집계된 tool 목록이 prefix 형태로 정확히 7개인지.
            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == EXPECTED, f"AC1 FAIL: {tools}"
            print(f"AC1 OK: {len(tools)} prefixed tools")

            # AC2: prefix 이름으로 호출이 올바른 백엔드(ticket)로 라우팅돼 쓰기가 되는지.
            created = await session.call_tool(
                "ticket__create_ticket", {"title": "compose check", "body": "ac8"}
            )
            assert not created.isError
            print(f"AC2 OK: create_ticket -> {json.loads(created.content[0].text)}")

            # AC3: 나머지 두 백엔드(docs/ops)도 Gateway 경유로 정상 응답하는지.
            d = await session.call_tool("docs__search_docs", {"query": "deployment"})
            o = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
            assert not d.isError and not o.isError
            print("AC3 OK: docs + ops via gateway")


asyncio.run(main())
