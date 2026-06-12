"""S5 관측 통합 — AC1(거부 메트릭 증가)·AC2(trace ID가 audit·로그에서 일치)."""

import json
import logging
import re

import httpx
from helpers import gateway, mcp_client

HOUR_ARGS = {"query": "error", "start": "2026-06-01T00:00:00", "end": "2026-06-01T01:00:00"}
DENIED_LABELS = {"agent": "support-agent", "server": "ops", "tool": "query_logs"}


def _metric_value(text: str, name: str, labels: dict) -> float:
    for line in text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def test_policy_denied_metric_increments(backends):
    async with gateway() as url:
        base = url.removesuffix("/mcp")
        async with httpx.AsyncClient() as client:
            before_text = (await client.get(f"{base}/metrics")).text
            before = _metric_value(before_text, "gateway_policy_denied_total", DENIED_LABELS)
            async with mcp_client(url, "support-agent") as session:
                denied = await session.call_tool("ops__query_logs", HOUR_ARGS)
                assert denied.isError
            after_text = (await client.get(f"{base}/metrics")).text
    assert _metric_value(after_text, "gateway_policy_denied_total", DENIED_LABELS) == before + 1
    calls = _metric_value(
        after_text, "gateway_tool_calls_total", {**DENIED_LABELS, "decision": "denied"}
    )
    assert calls >= 1  # 두 시스템(호출 카운터·거부 카운터)이 같은 사실을 본다


async def test_trace_id_in_audit_matches_gateway_log(backends, tmp_path, monkeypatch, caplog):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("GATEWAY_AUDIT_PATH", str(audit_path))
    with caplog.at_level(logging.INFO, logger="gateway.app"):
        async with gateway() as url, mcp_client(url, "dev-agent") as session:
            ok = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
            assert not ok.isError
    line = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert re.fullmatch(r"[0-9a-f]{32}", line["trace_id"])  # OTel trace ID 형식
    messages = [r.getMessage() for r in caplog.records if r.name == "gateway.app"]
    assert any(f"trace_id={line['trace_id']}" in m for m in messages)
