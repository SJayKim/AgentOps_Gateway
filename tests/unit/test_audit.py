"""audit.py 단위 — append·전 필드·256자 절단 + 쓰기 실패 허용 (AC 6d)."""

import json
import logging

from gateway import audit

FIELDS = {"ts", "agent", "tool", "args_summary", "decision", "trace_id"}


def test_append_two_lines_all_fields(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    audit.record(path, agent="dev-agent", tool="ticket__create_ticket", args={}, decision="allowed")
    audit.record(path, agent="anonymous", tool="docs__read_doc", args={}, decision="auth_failed")
    lines = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert len(lines) == 2
    for line in lines:
        assert set(line) == FIELDS
    assert lines[0]["decision"] == "allowed"
    assert lines[1]["decision"] == "auth_failed"


def test_args_summary_truncated_to_256(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    audit.record(path, agent="a", tool="t", args={"q": "x" * 1000}, decision="allowed")
    line = json.loads(open(path, encoding="utf-8").read())
    assert len(line["args_summary"]) == 256


def test_write_failure_logs_error_without_raising(tmp_path, caplog):
    # 디렉터리를 파일 경로로 주입 — open이 OSError. 가용성 > 감사 완결성
    with caplog.at_level(logging.ERROR, logger="gateway.audit"):
        audit.record(str(tmp_path), agent="a", tool="t", args={}, decision="allowed")
    assert any("audit write failed" in r.getMessage() for r in caplog.records)
