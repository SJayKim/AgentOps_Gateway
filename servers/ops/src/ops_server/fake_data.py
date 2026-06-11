"""가짜 운영 데이터 — 결정적 생성기 (시드 고정, 테스트 재현 가능)."""

import math
import random
from datetime import datetime, timedelta, timezone

VALID_METRICS = {"cpu", "memory", "requests"}

# 고정 기준 시각 — 매 호출 동일 결과 (재현성이 설계 제약)
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

_LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR"]
_COMPONENTS = ["gateway", "ticket-server", "docs-server", "ops-server", "auth"]
_MESSAGES = [
    "request completed",
    "connection pool exhausted",
    "retrying upstream call",
    "cache miss",
    "token validated",
    "slow query detected",
]


def get_metrics(metric: str) -> dict:
    if metric not in VALID_METRICS:
        raise ValueError(f"invalid metric {metric!r}: must be one of {sorted(VALID_METRICS)}")
    base = {"cpu": 40.0, "memory": 60.0, "requests": 200.0}[metric]
    points = [
        {
            "ts": (_BASE + timedelta(hours=i)).isoformat(),
            "value": round(base + 10 * math.sin(i / 3) + (i % 5), 2),
        }
        for i in range(24)
    ]
    return {"metric": metric, "points": points}


def _parse_iso(value: str, name: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid ISO8601 for {name!r}: {value!r}") from None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def query_logs(query: str, start: str, end: str) -> dict:
    start_ts = _parse_iso(start, "start")
    end_ts = _parse_iso(end, "end")
    if end_ts < start_ts:
        raise ValueError("end must not be before start")
    # 시간당 1줄, 시각 기반 시드로 결정적 생성. 범위 제한 없음 — 제한은 Gateway 정책(S4)의 몫.
    lines = []
    ts = start_ts.replace(minute=0, second=0, microsecond=0)
    while ts <= end_ts:
        rng = random.Random(int(ts.timestamp()))
        line = (
            f"{ts.isoformat()} {rng.choice(_LEVELS)} "
            f"[{rng.choice(_COMPONENTS)}] {rng.choice(_MESSAGES)}"
        )
        if query.lower() in line.lower():
            lines.append(line)
        ts += timedelta(hours=1)
    return {"lines": lines, "count": len(lines)}
