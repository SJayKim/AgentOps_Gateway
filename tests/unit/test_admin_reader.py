"""admin 리더 견고성(AC5b) + 24h 거부 집계(AC4) — 순수 함수, app 없이."""

import json
from datetime import datetime, timedelta, timezone

from gateway import admin

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(decision, agent="support-agent", tool="ops__query_logs", ts=NOW):
    return {
        "ts": ts.isoformat(),
        "agent": agent,
        "tool": tool,
        "args_summary": "{}",
        "decision": decision,
        "trace_id": "abc",
    }


# --- AC5b: 리더 견고성 ---


def test_read_missing_file_returns_empty(tmp_path):
    assert admin.read_audit(str(tmp_path / "nope.jsonl")) == []


def test_read_empty_file_returns_empty(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("", encoding="utf-8")
    assert admin.read_audit(str(p)) == []


def test_read_skips_corrupt_lines(tmp_path, caplog):
    p = tmp_path / "audit.jsonl"
    p.write_text(
        json.dumps(_row("denied"))
        + "\n"
        + '{"truncated": \n'  # 절단된 줄 (쓰기 중 중단 모사)
        + json.dumps(_row("allowed"))
        + "\n",
        encoding="utf-8",
    )
    rows = admin.read_audit(str(p))
    assert [r["decision"] for r in rows] == ["denied", "allowed"]  # 손상 줄만 제외


# --- AC4: 24h 거부 집계 ---


def test_summary_counts_only_recent_denials():
    rows = [
        _row("denied", agent="support-agent", tool="ops__query_logs"),
        _row("denied", agent="support-agent", tool="ops__get_metrics"),  # 같은 server
        _row("denied", agent="analyst-agent", tool="ticket__update_status"),
        _row("allowed"),  # allowed 제외
        _row("denied", ts=NOW - timedelta(hours=30)),  # 24h 밖 제외
    ]
    summary = admin.summarize_denials(rows, NOW)
    assert summary[("support-agent", "ops")] == 2
    assert summary[("analyst-agent", "ticket")] == 1
    assert sum(summary.values()) == 3


def test_filters_compose():
    rows = [
        _row("denied", agent="support-agent"),
        _row("allowed", agent="support-agent", tool="docs__search_docs"),
        _row("denied", agent="analyst-agent"),
    ]
    only_denied = admin.apply_filters(rows, decision="denied")
    assert all(r["decision"] == "denied" for r in only_denied)
    assert len(only_denied) == 2
    support_only = admin.apply_filters(rows, agent="support-agent")
    assert {r["agent"] for r in support_only} == {"support-agent"}
