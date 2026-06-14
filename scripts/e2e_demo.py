"""E2E 데모 클라이언트 — LLM 불필요 (Epic DoD 3, S5 P4).

[이 스크립트가 곧 Success Criteria]
design.md "Success Criteria": 스크립트 클라이언트(LLM 불필요)가 support-agent 토큰으로
ticket 생성 → docs 검색 → ops 호출 시도 → POLICY_DENIED 수신까지 E2E로 동작해야 한다.
이 파일이 바로 그 시나리오를 코드로 박제한 것이다. S6의 LangGraph 데모가 '실제 LLM 추론'으로
같은 길을 가기 전에, LLM 없이도 길 자체가 뚫려 있음을 증명하는 결정적 검증.

[성공-성공-거부, 그리고 거부가 곧 성공]
① ticket__create_ticket 성공 → ② docs__search_docs 성공 →
③ ops__query_logs → POLICY_DENIED 수신·파싱 → exit 0.
3번에서 '거부를 받는 것'이 시나리오의 성공이다. 거부가 안 나면(=정책 구멍) 오히려 실패(exit 1).

실행: GATEWAY_JWT_SECRET=<secret> uv run python scripts/e2e_demo.py
      (GATEWAY_URL 기본 http://localhost:8000/mcp)
"""

import asyncio
import json
import os
import sys

from issue_tokens import issue_token  # 발급과 검증이 같은 함수를 공유 — 토큰이 반드시 유효
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

AGENT = "support-agent"  # ops 접근이 '차단'되는 에이전트라야 3번 거부 장면이 나온다


def err_payload(result) -> dict:
    """isError result의 구조화 payload({"code", "rule", ...})를 파싱."""
    return json.loads(result.content[0].text)


async def main() -> int:
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    token = issue_token(AGENT, os.environ["GATEWAY_JWT_SECRET"])
    headers = {"Authorization": f"Bearer {token}"}  # 이 토큰이 Gateway auth를 통과해 agent_id가 된다
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"[e2e] connected to {url} as {AGENT}")

            # --- step 1: 허용된 쓰기(ticket) — 성공해야 정상 ---
            created = await session.call_tool(
                "ticket__create_ticket", {"title": "e2e demo", "body": "created by e2e_demo.py"}
            )
            if created.isError:
                print(f"[e2e] FAIL step 1 ticket__create_ticket: {err_payload(created)}")
                return 1
            print("[e2e] step 1 OK - ticket__create_ticket succeeded")

            # --- step 2: 허용된 읽기(docs) — 성공해야 정상 ---
            found = await session.call_tool("docs__search_docs", {"query": "deployment"})
            if found.isError:
                print(f"[e2e] FAIL step 2 docs__search_docs: {err_payload(found)}")
                return 1
            print("[e2e] step 2 OK - docs__search_docs succeeded")

            # --- step 3: 차단된 ops 접근 — '거부받는 것'이 성공 조건 ---
            denied = await session.call_tool(
                "ops__query_logs",
                {"query": "error", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"},
            )
            if not denied.isError:
                # 거부가 안 났다 = 정책이 새고 있다 → 시나리오 실패.
                print("[e2e] FAIL step 3 - ops__query_logs was allowed (expected POLICY_DENIED)")
                return 1
            payload = err_payload(denied)
            # 단순 에러가 아니라 '정책 거부' 코드인지까지 확인 — 에이전트가 파싱할 계약 검증.
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
    # exit code로 성공/실패를 알린다 — CI나 데모 스크립트가 결과를 기계적으로 판정할 수 있게.
    sys.exit(asyncio.run(main()))
