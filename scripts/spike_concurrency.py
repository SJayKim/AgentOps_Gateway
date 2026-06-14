"""S3 Day-1 스파이크 — ClientSession 1개 위 동시 10호출 안전성 검증 (eng review 이슈 3).

[왜 이 스파이크가 먼저였나]
upstream.py의 "백엔드당 세션 1개를 요청 task들이 공유한다"는 설계는 ClientSession 하나에
동시 호출을 꽂아도 응답이 섞이거나 데드락 나지 않아야 성립한다. 그 전제가 틀리면 설계
전체를 다시 짜야 하므로, 코드를 본격적으로 쌓기 전에 가장 위험한 가정을 먼저 찔러 본 것이다
(Day-1 스파이크). ticket-server(:8101)만 기동된 상태에서 단독 실행한다.
"""

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CONCURRENCY = 10


def payload(result) -> dict:
    """tool result의 첫 텍스트 블록을 JSON으로 파싱(create_ticket의 dict 반환)."""
    return json.loads(result.content[0].text)


async def main() -> None:
    # 세션을 '하나'만 연다 — 이게 검증 대상이다(풀이 아니라 단일 세션 공유).
    async with streamablehttp_client("http://localhost:8101/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            async def call(i: int) -> dict:
                result = await session.call_tool(
                    "create_ticket", {"title": f"spike-{i}", "body": f"body-{i}"}
                )
                assert not result.isError, f"call {i} errored: {result}"
                return payload(result)

            # 핵심: 같은 session에 10개 호출을 동시에(gather) 꽂는다.
            results = await asyncio.gather(*(call(i) for i in range(CONCURRENCY)))

            # 검증 1) id가 전부 유일 → 응답이 뒤섞이지 않고 각 INSERT가 독립적으로 처리됨.
            ids = [r["id"] for r in results]
            assert len(set(ids)) == CONCURRENCY, f"duplicate ids: {ids}"

            # 검증 2) 응답-요청 매칭: 각 spike-i가 실제로 자기 제목으로 검색되는지 확인 —
            # 동시 호출이 서로의 인자를 밟지 않았다는 더 강한 보증(list[dict] 반환은 항목별
            # TextContent 리스트로 온다).
            for i, r in enumerate(results):
                found = await session.call_tool("search_tickets", {"query": f"spike-{i}"})
                titles = [json.loads(c.text)["title"] for c in found.content]
                assert f"spike-{i}" in titles, f"spike-{i} not found: {titles}"

            print(f"OK: {CONCURRENCY} concurrent calls on one ClientSession, ids={sorted(ids)}")


asyncio.run(main())
