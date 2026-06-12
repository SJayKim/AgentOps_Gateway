"""E2E 데모 클라이언트 — LLM 불필요 (Epic DoD 3, S5 P4).

support-agent 토큰으로 성공-성공-거부 시나리오를 완주한다:
① ticket__create_ticket 성공 → ② docs__search_docs 성공 →
③ ops__query_logs → POLICY_DENIED 수신·파싱 → exit 0 (거부가 시나리오 성공).

실행: GATEWAY_JWT_SECRET=<secret> uv run python scripts/e2e_demo.py
      (GATEWAY_URL 기본 http://localhost:8000/mcp)
"""

import asyncio
import json
import os
import sys

from issue_tokens import issue_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

AGENT = "support-agent"


def err_payload(result) -> dict:
    return json.loads(result.content[0].text)


async def main() -> int:
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    token = issue_token(AGENT, os.environ["GATEWAY_JWT_SECRET"])
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"[e2e] connected to {url} as {AGENT}")

            created = await session.call_tool(
                "ticket__create_ticket", {"title": "e2e demo", "body": "created by e2e_demo.py"}
            )
            if created.isError:
                print(f"[e2e] FAIL step 1 ticket__create_ticket: {err_payload(created)}")
                return 1
            print("[e2e] step 1 OK - ticket__create_ticket succeeded")

            found = await session.call_tool("docs__search_docs", {"query": "deployment"})
            if found.isError:
                print(f"[e2e] FAIL step 2 docs__search_docs: {err_payload(found)}")
                return 1
            print("[e2e] step 2 OK - docs__search_docs succeeded")

            denied = await session.call_tool(
                "ops__query_logs",
                {"query": "error", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"},
            )
            if not denied.isError:
                print("[e2e] FAIL step 3 - ops__query_logs was allowed (expected POLICY_DENIED)")
                return 1
            payload = err_payload(denied)
            if payload.get("code") != "POLICY_DENIED":
                print(f"[e2e] FAIL step 3 - expected POLICY_DENIED, got {payload}")
                return 1
            print(
                "[e2e] step 3 OK - ops__query_logs denied "
                f"(code={payload['code']}, rule={payload['rule']})"
            )

    print("[e2e] scenario complete: success-success-denied")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
