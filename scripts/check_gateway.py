"""compose 기동 상태에서 Gateway(:8000) 경유 AC1-3 수동 확인 (check_servers.py의 짝).

check_servers가 백엔드에 직접 붙었다면, 이 스크립트는 Gateway를 통해서 본다 — 그래서
tool 이름이 prefix가 붙은 형태(ticket__create_ticket 등)로 나와야 하고, 그게 곧 집계·
라우팅이 동작한다는 증거다. Gateway는 initialize/tools-list 같은 비-tools/call 요청을
미인증이면 HTTP 401로 끊으므로(app.py 이중 계층 auth), dev-agent 토큰으로 붙는다.
dev-agent를 쓰는 이유: 아래 호출(create_ticket·search_docs·get_metrics)이 전부 허용돼
어떤 호출도 정책 거부에 걸리지 않는 유일한 에이전트라, 이 스크립트가 보려는 '집계·중계
경로'가 정책 거부에 가려지지 않는다. 정책 거부 시나리오는 e2e_demo.py가 따로 맡는다.

실행: GATEWAY_JWT_SECRET=<secret> uv run python scripts/check_gateway.py
"""

import asyncio
import json
import os

from issue_tokens import issue_token  # 발급과 검증이 같은 함수를 공유 — 토큰이 반드시 유효
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

AGENT = "dev-agent"  # ticket·docs·ops 전 tool 허용 — 아래 어떤 호출도 정책 거부에 안 걸린다

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
    token = issue_token(AGENT, os.environ["GATEWAY_JWT_SECRET"])
    headers = {"Authorization": f"Bearer {token}"}  # initialize/tools-list가 401에 안 막히게
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
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
