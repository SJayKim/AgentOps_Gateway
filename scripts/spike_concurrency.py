"""S3 Day-1 스파이크 — ClientSession 1개 위 동시 10호출 안전성 검증 (eng review 이슈 3).

ticket-server(:8101) 기동 상태에서 실행. 세션 공유 설계의 전제 검증:
streamablehttp_client + ClientSession 하나를 asyncio.gather 동시 호출이
공유해도 응답이 섞이거나 데드락 없이 전부 성공해야 한다.
"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CONCURRENCY = 10


def payload(result) -> dict:
    return json.loads(result.content[0].text)


async def main() -> None:
    async with streamablehttp_client("http://localhost:8101/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            async def call(i: int) -> dict:
                result = await session.call_tool(
                    "create_ticket", {"title": f"spike-{i}", "body": f"body-{i}"}
                )
                assert not result.isError, f"call {i} errored: {result}"
                return payload(result)

            results = await asyncio.gather(*(call(i) for i in range(CONCURRENCY)))

            ids = [r["id"] for r in results]
            assert len(set(ids)) == CONCURRENCY, f"duplicate ids: {ids}"

            # 응답-요청 매칭 확인: 각 응답이 자기 요청 내용과 일치하는지
            # (list[dict] 반환은 content가 항목별 TextContent 리스트)
            for i, r in enumerate(results):
                found = await session.call_tool("search_tickets", {"query": f"spike-{i}"})
                titles = [json.loads(c.text)["title"] for c in found.content]
                assert f"spike-{i}" in titles, f"spike-{i} not found: {titles}"

            print(f"OK: {CONCURRENCY} concurrent calls on one ClientSession, ids={sorted(ids)}")


asyncio.run(main())
