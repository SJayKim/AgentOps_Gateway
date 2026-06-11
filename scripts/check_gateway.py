"""compose 기동 상태에서 Gateway(:8000) 경유 AC1-3 수동 확인 (check_servers.py의 짝)."""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

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

            tools = {t.name for t in (await session.list_tools()).tools}
            assert tools == EXPECTED, f"AC1 FAIL: {tools}"
            print(f"AC1 OK: {len(tools)} prefixed tools")

            created = await session.call_tool(
                "ticket__create_ticket", {"title": "compose check", "body": "ac8"}
            )
            assert not created.isError
            print(f"AC2 OK: create_ticket -> {json.loads(created.content[0].text)}")

            d = await session.call_tool("docs__search_docs", {"query": "deployment"})
            o = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
            assert not d.isError and not o.isError
            print("AC3 OK: docs + ops via gateway")


asyncio.run(main())
