"""k8s/base/config/ 사본이 원본과 갈라지지 않는지 고정.

kustomize는 kustomization 루트 밖의 파일을 거부한다("security; file is not in or below").
그래서 ConfigMap 소스를 k8s/base/config/에 복사해 두고 있는데, 사본은 조용히 썩는다 —
policies/policy.yaml은 권한 매트릭스라 갈라지면 클러스터와 compose의 정책이 달라진다.
여기서 막는다.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PAIRS = [
    ("policies/policy.yaml", "policy.yaml"),
    ("observability/prometheus.yml", "prometheus.yml"),
    (
        "observability/grafana/provisioning/datasources/prometheus.yml",
        "grafana-datasources.yml",
    ),
    (
        "observability/grafana/provisioning/dashboards/dashboards.yml",
        "grafana-dashboards.yml",
    ),
    ("observability/grafana/dashboards/gateway.json", "grafana-dashboard-gateway.json"),
]


@pytest.mark.parametrize("source, copy", PAIRS)
def test_k8s_config_copy_matches_source(source: str, copy: str) -> None:
    src = (ROOT / source).read_text(encoding="utf-8")
    dst = (ROOT / "k8s/base/config" / copy).read_text(encoding="utf-8")
    assert dst == src, (
        f"k8s/base/config/{copy}가 {source}와 다르다 — 원본을 고쳤으면 사본도 갱신할 것"
    )
