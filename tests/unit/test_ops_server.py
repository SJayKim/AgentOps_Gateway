import pytest

from ops_server import fake_data


def test_get_metrics_deterministic_points():
    first = fake_data.get_metrics("cpu")
    assert first["metric"] == "cpu"
    assert len(first["points"]) >= 1
    assert first == fake_data.get_metrics("cpu")


def test_get_metrics_invalid_metric():
    with pytest.raises(ValueError, match="invalid metric"):
        fake_data.get_metrics("disk")


def test_query_logs_over_24h_is_allowed():
    # 서버는 범위를 제한하지 않는다 — 제한은 Gateway 정책(S4)의 몫
    result = fake_data.query_logs("", "2026-01-01T00:00:00", "2026-01-04T00:00:00")
    assert result["count"] == len(result["lines"]) == 73  # 72시간 + 양끝 포함
    assert result == fake_data.query_logs("", "2026-01-01T00:00:00", "2026-01-04T00:00:00")


def test_query_logs_invalid_iso8601():
    with pytest.raises(ValueError, match="invalid ISO8601"):
        fake_data.query_logs("", "yesterday", "2026-01-02T00:00:00")
