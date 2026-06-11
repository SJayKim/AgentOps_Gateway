"""MCP 클라이언트 → 서버 라운드트립 (AC1-5). in-memory 세션이라 CI에서 도커 없이 돈다."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as client_session

from docs_server.server import mcp as docs_mcp
from ops_server.server import mcp as ops_mcp
from ticket_server.server import mcp as ticket_mcp

EXPECTED_TOOLS = {
    "ticket-server": {
        "create_ticket": {"title", "body"},
        "search_tickets": {"query"},
        "update_status": {"ticket_id", "status"},
    },
    "docs-server": {
        "search_docs": {"query"},
        "read_doc": {"doc_id"},
    },
    "ops-server": {
        "get_metrics": {"metric"},
        "query_logs": {"query", "start", "end"},
    },
}

SERVERS = {"ticket-server": ticket_mcp, "docs-server": docs_mcp, "ops-server": ops_mcp}


def payload(result):
    # mcp 1.27: list[dict] 반환은 structuredContent 생성, bare dict는 텍스트 JSON만
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TICKET_DB_PATH", str(tmp_path / "tickets.db"))


async def test_tools_list_matches_spec():
    for name, server in SERVERS.items():
        async with client_session(server._mcp_server) as session:
            tools = (await session.list_tools()).tools
        by_name = {t.name: t for t in tools}
        assert set(by_name) == set(EXPECTED_TOOLS[name]), name
        for tool_name, params in EXPECTED_TOOLS[name].items():
            schema = by_name[tool_name].inputSchema
            assert set(schema["properties"]) == params, f"{name}.{tool_name}"
            assert set(schema.get("required", [])) == params, f"{name}.{tool_name}"


async def test_ticket_roundtrip_and_invalid_status():
    async with client_session(ticket_mcp._mcp_server) as session:
        created = await session.call_tool(
            "create_ticket", {"title": "gateway timeout", "body": "504 on /chat"}
        )
        assert not created.isError
        ticket_id = payload(created)["id"]

        found = await session.call_tool("search_tickets", {"query": "gateway timeout"})
        assert not found.isError
        assert any(t["id"] == ticket_id for t in payload(found)["result"])

        updated = await session.call_tool(
            "update_status", {"ticket_id": ticket_id, "status": "closed"}
        )
        assert not updated.isError
        assert payload(updated)["status"] == "closed"

        bad = await session.call_tool("update_status", {"ticket_id": ticket_id, "status": "done"})
        assert bad.isError


async def test_docs_search_read_and_missing_doc():
    async with client_session(docs_mcp._mcp_server) as session:
        found = await session.call_tool("search_docs", {"query": "deployment"})
        assert not found.isError
        hits = payload(found)["result"]
        assert len(hits) >= 1

        doc = await session.call_tool("read_doc", {"doc_id": hits[0]["doc_id"]})
        assert not doc.isError
        assert payload(doc)["doc_id"] == hits[0]["doc_id"]
        assert len(payload(doc)["content"]) > 0

        missing = await session.call_tool("read_doc", {"doc_id": "no-such-doc"})
        assert missing.isError


async def test_ops_metrics_and_unlimited_log_range():
    async with client_session(ops_mcp._mcp_server) as session:
        metrics = await session.call_tool("get_metrics", {"metric": "cpu"})
        assert not metrics.isError
        assert len(payload(metrics)["points"]) >= 1

        # 24시간 초과 범위도 정상 처리 — 서버는 제한하지 않는다 (제한은 S4 Gateway 정책)
        logs = await session.call_tool(
            "query_logs",
            {"query": "", "start": "2026-01-01T00:00:00", "end": "2026-01-05T00:00:00"},
        )
        assert not logs.isError
        assert payload(logs)["count"] > 24

        bad = await session.call_tool(
            "query_logs", {"query": "", "start": "not-a-date", "end": "2026-01-02T00:00:00"}
        )
        assert bad.isError
