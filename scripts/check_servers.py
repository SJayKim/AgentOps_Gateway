"""기동 중인 백엔드 3종에 HTTP로 tools/list 호출 — AC1/AC6 수동 확인용.

Gateway를 거치지 않고 백엔드에 '직접' 붙어 각 서버가 어떤 tool을 노출하는지 눈으로
확인하는 진단 스크립트. 자동화 테스트가 아니라 사람이 기동 직후 손으로 돌려보는 용도다
(check_gateway.py가 Gateway 경유 짝).
"""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def check(port: int) -> None:
    """한 백엔드에 붙어 tool 이름 목록을 출력한다(prefix 없는 '원본' 이름)."""
    async with streamablehttp_client(f"http://localhost:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f":{port} -> {[t.name for t in tools]}")


async def main() -> None:
    # 8101=ticket, 8102=docs, 8103=ops — BACKEND_SPECS의 포트와 일치.
    for port in (8101, 8102, 8103):
        await check(port)


asyncio.run(main())
