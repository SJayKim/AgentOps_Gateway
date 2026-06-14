"""가짜 운영 데이터 — 결정적(deterministic) 생성기. 시드를 시각에 고정해 매 호출 같은 결과.

[왜 결정적인가 — design.md 제약]
"외부 의존성 없는 완전 통제 가능한 시뮬레이션 환경"이 제약이다. 실제 운영 DB·로그 수집기에
붙는 대신 코드가 데이터를 만들어 낸다. 이때 무작위가 매번 다르면 테스트가 값을 단언할 수
없으므로, 같은 입력엔 항상 같은 출력이 나오도록 기준 시각·시드를 고정했다(재현성이 설계 제약).
"""

import math
import random
from datetime import datetime, timedelta, timezone

VALID_METRICS = {"cpu", "memory", "requests"}  # get_metrics 화이트리스트 — 그 외 입력은 거절

# 고정 기준 시각. get_metrics가 이 시각부터 24시간을 생성하므로 호출마다 동일한 시계열이 나온다.
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# 로그 합성용 어휘 풀. INFO를 3번 넣어 가중치를 줬다 — 실제 로그처럼 INFO가 다수, WARN/ERROR가
# 소수가 되게 해 '그럴듯함'을 높인다.
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
    """주어진 metric의 24시간치 시계열(시간당 1포인트)을 반환. 미지원 metric은 ValueError."""
    if metric not in VALID_METRICS:
        raise ValueError(f"invalid metric {metric!r}: must be one of {sorted(VALID_METRICS)}")
    base = {"cpu": 40.0, "memory": 60.0, "requests": 200.0}[metric]  # metric별 기준선
    points = [
        {
            "ts": (_BASE + timedelta(hours=i)).isoformat(),
            # 기준선 + 사인파(완만한 주기적 변동) + (i%5)(작은 톱니) — 평평하지 않고 살아있는
            # 그래프처럼 보이게 하는 결정적 합성. 무작위가 아니라 i의 함수라 항상 같은 곡선.
            "value": round(base + 10 * math.sin(i / 3) + (i % 5), 2),
        }
        for i in range(24)
    ]
    return {"metric": metric, "points": points}


def _parse_iso(value: str, name: str) -> datetime:
    """ISO8601 문자열을 datetime으로. tz 없으면 UTC로 간주(naive/aware 비교 오류 예방)."""
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid ISO8601 for {name!r}: {value!r}") from None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def query_logs(query: str, start: str, end: str) -> dict:
    """[start, end] 구간에서 query를 포함하는 로그 줄을 합성해 반환한다. 범위 제한은 없다(정책의 몫)."""
    start_ts = _parse_iso(start, "start")
    end_ts = _parse_iso(end, "end")
    if end_ts < start_ts:
        raise ValueError("end must not be before start")  # 무의미한 구간은 백엔드 차원에서 거절
    # 시간당 1줄을 만들되, 각 줄을 '그 시각'을 시드로 한 RNG로 생성한다 → 같은 시각은 항상 같은
    # 줄. 그래서 겹치는 구간을 두 번 조회해도 동일 줄이 나온다(결정성). 범위 제한이 없는 건 의도:
    # 시간 범위 제한은 Gateway 정책(S4)이 강제하지 백엔드가 하지 않는다.
    lines = []
    ts = start_ts.replace(minute=0, second=0, microsecond=0)  # 정시 단위로 정렬
    while ts <= end_ts:
        rng = random.Random(int(ts.timestamp()))  # 시각 기반 시드 — 시각이 곧 그 줄의 결정자
        line = (
            f"{ts.isoformat()} {rng.choice(_LEVELS)} "
            f"[{rng.choice(_COMPONENTS)}] {rng.choice(_MESSAGES)}"
        )
        if query.lower() in line.lower():  # 대소문자 무시 substring 필터
            lines.append(line)
        ts += timedelta(hours=1)
    return {"lines": lines, "count": len(lines)}
