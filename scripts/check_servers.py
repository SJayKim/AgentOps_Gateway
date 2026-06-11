"""기동 중인 백엔드 3종에 HTTP로 tools/list 호출 — AC1/AC6 수동 확인용."""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def check(port: int) -> None:
    async with streamablehttp_client(f"http://localhost:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f":{port} -> {[t.name for t in tools]}")


async def main() -> None:
    for port in (8101, 8102, 8103):
        await check(port)


asyncio.run(main())
