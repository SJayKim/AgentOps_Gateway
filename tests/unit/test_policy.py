"""policy.py 단위 — default-deny, 인자 정책(경계 포함), 이상 입력 3종, 로드 시 검증."""

import logging

import pytest

from gateway.policy import Policy

POLICY_YAML = """
support-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
analyst-agent:
  ticket: [search_tickets]
  docs: [search_docs, read_doc]
  ops:
    - get_metrics
    - tool: query_logs
      max_range_hours: 24
dev-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
  ops: [get_metrics, query_logs]
"""


@pytest.fixture
def policy(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(POLICY_YAML, encoding="utf-8")
    return Policy.load(str(f))


def test_listed_tool_allowed(policy):
    d = policy.evaluate("support-agent", "ticket", "create_ticket", {})
    assert d.allowed
    assert d.rule == "support-agent:ticket:create_ticket"


def test_default_deny_unlisted_server(policy):
    # support-agent는 ops 미기재 = 전부 거부
    d = policy.evaluate("support-agent", "ops", "query_logs", {})
    assert not d.allowed
    assert d.rule == "support-agent:ops:query_logs"


def test_default_deny_write_tools_in_read_cell(policy):
    # analyst×ticket은 "읽기" 칸 — search_tickets만 허용
    assert not policy.evaluate("analyst-agent", "ticket", "create_ticket", {}).allowed
    assert not policy.evaluate("analyst-agent", "ticket", "update_status", {}).allowed
    assert policy.evaluate("analyst-agent", "ticket", "search_tickets", {}).allowed


def test_default_deny_unregistered_agent(policy):
    for server, tool in [("ticket", "search_tickets"), ("docs", "search_docs")]:
        assert not policy.evaluate("rogue-agent", server, tool, {}).allowed


RANGE_24H = {"start": "2026-06-01T00:00:00", "end": "2026-06-02T00:00:00"}
RANGE_26H = {"start": "2026-06-01T00:00:00", "end": "2026-06-02T02:00:00"}


def test_arg_policy_exactly_24h_allowed(policy):
    # 경계값 — <= 계약 고정
    assert policy.evaluate("analyst-agent", "ops", "query_logs", RANGE_24H).allowed


def test_arg_policy_over_24h_denied_with_detail(policy):
    d = policy.evaluate("analyst-agent", "ops", "query_logs", RANGE_26H)
    assert not d.allowed
    assert d.detail == "time range 26h exceeds max 24h"


def test_arg_policy_under_24h_allowed(policy):
    args = {"start": "2026-06-01T00:00:00", "end": "2026-06-01T02:00:00"}
    assert policy.evaluate("analyst-agent", "ops", "query_logs", args).allowed


def test_arg_policy_not_applied_to_dev_agent(policy):
    # dev-agent는 읽기+쓰기 — 동일 26h 호출 성공 (매트릭스 차등의 시연)
    assert policy.evaluate("dev-agent", "ops", "query_logs", RANGE_26H).allowed


def test_arg_policy_malformed_inputs_deny_without_crash(policy):
    # 이상 입력 3종 (AC 6b): 파싱 불가 / end < start / naive·aware 혼용
    cases = [
        {"start": "not-a-date", "end": "2026-06-01T00:00:00"},
        {"start": "2026-06-02T00:00:00", "end": "2026-06-01T00:00:00"},
        {"start": "2026-06-01T00:00:00", "end": "2026-06-01T12:00:00+00:00"},
        {},  # 인자 자체가 없음
    ]
    for args in cases:
        d = policy.evaluate("analyst-agent", "ops", "query_logs", args)
        assert not d.allowed, args
        assert d.detail, args


def test_load_warns_on_unknown_tool_names(tmp_path, caplog):
    # AC 6c: default-deny에서 YAML 오타는 조용한 거부 — 로드 시 경고로 가시화
    f = tmp_path / "policy.yaml"
    f.write_text("support-agent:\n  ticket: [create_tikcet]\n  nosuch: [x]\n", encoding="utf-8")
    p = Policy.load(str(f))
    known = {"ticket": {"create_ticket", "search_tickets", "update_status"}}
    with caplog.at_level(logging.WARNING, logger="gateway.policy"):
        p.warn_unknown_tools(known)
    assert any("create_tikcet" in r.getMessage() for r in caplog.records)
    assert any("nosuch" in r.getMessage() for r in caplog.records)
