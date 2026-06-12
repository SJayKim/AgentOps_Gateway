"""인증 이중 계층(AC4) + 인자 정책 경계(AC3) + default-deny(AC5) + audit 4종(AC6).

tools/call의 인증 실패는 MCP isError로 받아야 하므로, 유효 토큰으로 세션을
연 뒤 같은 session-id로 무효 인증의 raw POST를 보내 검증한다 (S6 파싱 계약).
"""

import json
import os

import httpx
from helpers import auth_headers, err_payload, gateway, mcp_client, token
from issue_tokens import issue_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HOUR_ARGS = {"query": "error", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"}


# --- AC3: 인자 레벨 정책 (경계값 포함) ---


async def test_arg_policy_analyst_boundary_and_dev_exempt(backends):
    range_26h = {"query": "e", "start": "2026-06-01T00:00:00", "end": "2026-06-02T02:00:00"}
    range_24h = {"query": "e", "start": "2026-06-01T00:00:00", "end": "2026-06-02T00:00:00"}
    async with gateway() as url:
        async with mcp_client(url, "analyst-agent") as session:
            over = await session.call_tool("ops__query_logs", range_26h)
            assert err_payload(over) == {
                "code": "POLICY_DENIED",
                "rule": "analyst-agent:ops:query_logs",
                "agent": "analyst-agent",
                "detail": "time range 26h exceeds max 24h",
            }
            exact = await session.call_tool("ops__query_logs", range_24h)
            assert not exact.isError  # 정확히 24h → 성공 (<= 계약 고정)
            under = await session.call_tool("ops__query_logs", HOUR_ARGS)
            assert not under.isError
        async with mcp_client(url, "dev-agent") as session:
            dev = await session.call_tool("ops__query_logs", range_26h)
            assert not dev.isError  # dev-agent는 동일 26h도 성공 — 매트릭스 차등


# --- AC4: 인증 3분기 (tools/call은 MCP isError, 비-tool-call은 HTTP 401) ---


def _tools_call_message(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _sse_result(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))["result"]
    raise AssertionError(f"no SSE data line in: {text!r}")


async def test_tools_call_auth_three_branches(backends):
    bad_headers = {
        "missing": {},
        "invalid": {
            "Authorization": f"Bearer {issue_token('dev-agent', 'wrong-secret-0123456789abcdef0123456789abcdef')}"
        },
        "expired": {
            "Authorization": f"Bearer {issue_token('dev-agent', os.environ['GATEWAY_JWT_SECRET'], days=-1)}"
        },
    }
    async with gateway() as url:
        # 유효 토큰으로 세션 확보 — 이후 raw POST가 session-id를 재사용
        async with streamablehttp_client(url, headers=auth_headers("dev-agent")) as (
            read,
            write,
            get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                session_id = get_session_id()
                async with httpx.AsyncClient() as client:
                    for reason, headers in bad_headers.items():
                        response = await client.post(
                            url,
                            json=_tools_call_message("ops__get_metrics", {"metric": "cpu"}),
                            headers={
                                "accept": "application/json, text/event-stream",
                                "mcp-session-id": session_id,
                                **headers,
                            },
                        )
                        assert response.status_code == 200, (reason, response.text)
                        result = _sse_result(response.text)
                        assert result["isError"] is True, reason
                        payload = json.loads(result["content"][0]["text"])
                        assert payload == {"code": "AUTH_FAILED", "reason": reason}


async def test_tools_list_without_token_is_http_401(backends):
    async with gateway() as url:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"accept": "application/json, text/event-stream"},
            )
            assert response.status_code == 401
            assert response.json() == {"code": "AUTH_FAILED", "reason": "missing"}


# --- AC5: 미등록 agent_id — default-deny ---


async def test_unregistered_agent_denied_everywhere(backends):
    async with gateway() as url, mcp_client(url, "rogue-agent") as session:
        for tool, args in [
            ("ticket__search_tickets", {"query": "x"}),
            ("docs__search_docs", {"query": "deployment"}),
            ("ops__get_metrics", {"metric": "cpu"}),
        ]:
            result = await session.call_tool(tool, args)
            assert err_payload(result)["code"] == "POLICY_DENIED", tool


# --- AC6: audit JSONL — 4종 decision 각 1줄, 전 필드, 256자 절단 ---


async def test_audit_records_four_decisions(backends, tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("GATEWAY_AUDIT_PATH", str(audit_path))
    async with gateway() as url:
        async with mcp_client(url, "dev-agent") as session:
            ok = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
            assert not ok.isError
            unknown = await session.call_tool("ticket__nonexistent", {})
            assert err_payload(unknown)["code"] == "UNKNOWN_TOOL"
        async with mcp_client(url, "support-agent") as session:
            denied = await session.call_tool("ops__query_logs", HOUR_ARGS)
            assert err_payload(denied)["code"] == "POLICY_DENIED"
        # 인증 실패 1건 — 만료 토큰의 raw tools/call
        async with streamablehttp_client(url, headers=auth_headers("dev-agent")) as (
            read,
            write,
            get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async with httpx.AsyncClient() as client:
                    expired = issue_token("dev-agent", os.environ["GATEWAY_JWT_SECRET"], days=-1)
                    await client.post(
                        url,
                        json=_tools_call_message("ops__get_metrics", {"metric": "cpu"}),
                        headers={
                            "accept": "application/json, text/event-stream",
                            "mcp-session-id": get_session_id(),
                            "Authorization": f"Bearer {expired}",
                        },
                    )
    lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    by_decision = {line["decision"]: line for line in lines}
    assert set(by_decision) == {"allowed", "denied", "auth_failed", "error"}
    for line in lines:
        assert set(line) == {"ts", "agent", "tool", "args_summary", "decision", "trace_id"}
        assert len(line["args_summary"]) <= 256
    assert by_decision["allowed"]["agent"] == "dev-agent"
    assert by_decision["denied"]["tool"] == "ops__query_logs"
    assert by_decision["auth_failed"]["agent"] == "anonymous"
    assert by_decision["error"]["tool"] == "ticket__nonexistent"


# --- AC7: scripts/issue_tokens.py 출력 3개가 픽스처로 동작 ---


def test_issue_tokens_script_emits_three_usable_tokens():
    from gateway import auth

    for agent in ("support-agent", "analyst-agent", "dev-agent"):
        assert auth.authenticate(f"Bearer {token(agent)}") == agent
