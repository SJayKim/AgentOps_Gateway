"""record_call 단위 — decision 4종의 메트릭 레이블 정확성 (S5 Testing Plan)."""

import pytest
from prometheus_client import REGISTRY

from gateway import observability

LABELS = {"agent": "support-agent", "server": "ops", "tool": "query_logs"}


def _value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.parametrize("decision", ["allowed", "denied", "auth_failed", "error"])
def test_decision_label_increments_counters(decision):
    call_labels = {**LABELS, "decision": decision}
    calls_before = _value("gateway_tool_calls_total", call_labels)
    denied_before = _value("gateway_policy_denied_total", LABELS)
    observability.record_call(
        agent="support-agent", server="ops", tool="query_logs", decision=decision
    )
    assert _value("gateway_tool_calls_total", call_labels) == calls_before + 1
    # 핵심 메트릭은 denied에서만 증가
    expected = 1 if decision == "denied" else 0
    assert _value("gateway_policy_denied_total", LABELS) == denied_before + expected


def test_duration_observed_only_when_given():
    labels = {"server": "docs", "tool": "search_docs"}
    count_before = _value("gateway_tool_call_duration_seconds_count", labels)
    observability.record_call(
        agent="a", server="docs", tool="search_docs", decision="auth_failed"
    )  # duration 없음 (인증 실패는 라우팅 미진입)
    assert _value("gateway_tool_call_duration_seconds_count", labels) == count_before
    observability.record_call(
        agent="a", server="docs", tool="search_docs", decision="allowed", duration_s=0.05
    )
    assert _value("gateway_tool_call_duration_seconds_count", labels) == count_before + 1
