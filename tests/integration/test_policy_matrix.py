"""AC1: 9칸 권한 매트릭스 — 셀당 대표 tool 1개 + 셀 내부 쓰기 차등 2건 (11 케이스).

S5 진입 조건인 spec 핵심 테스트. POLICY_DENIED payload는 S6와의 고정 계약.
"""

from helpers import err_payload, gateway, mcp_client

# (agent, 셀 대표 허용 tool, 인자) — 허용 8칸
ALLOWED_CELLS = [
    ("support-agent", "ticket__create_ticket", {"title": "t", "body": "b"}),
    ("support-agent", "docs__search_docs", {"query": "deployment"}),
    ("analyst-agent", "ticket__search_tickets", {"query": "x"}),
    ("analyst-agent", "docs__read_doc", {"doc_id": "incident-runbook"}),
    ("analyst-agent", "ops__get_metrics", {"metric": "cpu"}),
    ("dev-agent", "ticket__update_status", {"ticket_id": 1, "status": "closed"}),
    ("dev-agent", "docs__search_docs", {"query": "deployment"}),
    (
        "dev-agent",
        "ops__query_logs",
        {"query": "error", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"},
    ),
]


async def test_allowed_eight_cells(backends):
    async with gateway() as url:
        for agent, tool, args in ALLOWED_CELLS:
            async with mcp_client(url, agent) as session:
                result = await session.call_tool(tool, args)
                assert not result.isError, (agent, tool, result.content)


async def test_forbidden_cell_support_ops(backends):
    # 금지 1칸: support×ops — payload가 형식 계약과 정확히 일치 (AC2)
    async with gateway() as url, mcp_client(url, "support-agent") as session:
        result = await session.call_tool(
            "ops__query_logs",
            {"query": "e", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"},
        )
        assert err_payload(result) == {
            "code": "POLICY_DENIED",
            "rule": "support-agent:ops:query_logs",
            "agent": "support-agent",
        }


async def test_read_cell_denies_write_tools(backends):
    # analyst×ticket은 "읽기" 칸 — create/update 거부 (셀 내부 쓰기 차등 2건)
    async with gateway() as url, mcp_client(url, "analyst-agent") as session:
        create = await session.call_tool("ticket__create_ticket", {"title": "t", "body": "b"})
        assert err_payload(create)["rule"] == "analyst-agent:ticket:create_ticket"
        update = await session.call_tool(
            "ticket__update_status", {"ticket_id": 1, "status": "closed"}
        )
        assert err_payload(update)["rule"] == "analyst-agent:ticket:update_status"
