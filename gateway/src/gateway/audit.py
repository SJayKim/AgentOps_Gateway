"""audit log — append-only JSONL. 한 번 쓴 줄을 수정·삭제하는 코드 경로가 아예 없다.

[왜 append-only JSONL인가]
감사 로그의 가치는 "누가 무엇에 접근을 시도했나"를 사후에 신뢰할 수 있게 보존하는 것이다
(design.md 전제 4: 거부 + audit log가 가장 설득력 있는 산출물). 수정·삭제 경로가 없는
append-only 파일이면 그 자체로 "변조 안 됨"의 약한 보증이 되고, DB·인덱스 없이 한 줄씩
append만 하면 돼서 구현이 단순하다. admin 페이지(S6)도 이 파일을 직접 읽는다.

[decision enum을 메트릭과 공유하는 이유]
decision은 allowed | denied | auth_failed | error 4종 — observability.py 메트릭의
라벨과 정확히 동일하다 (eng review 이슈 2). 감사 로그와 메트릭이 같은 어휘로 같은 사실을
기록해야, 대시보드의 "거부 12건"과 audit의 "denied 12줄"이 어긋나지 않는다.

[기록 실패 시 호출을 막지 않는 이유]
디스크 오류로 audit 쓰기가 실패해도 tool 호출 자체는 진행시킨다(에러 로그만 남김).
데모 기준에선 가용성 > 감사 완결성 — 감사 못 남겼다고 정상 요청을 거부하면 손해가 더 크다.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def record(path: str, *, agent: str, tool: str, args: dict, decision: str, trace_id: str) -> None:
    """tool 호출 한 건을 audit JSONL에 한 줄 append한다."""
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),  # UTC ISO8601 — admin 시간 필터의 기준
        "agent": agent,
        "tool": tool,  # prefix 포함 전체 이름(예: ops__query_logs). admin이 prefix로 서버를 도로 추출한다
        "args_summary": json.dumps(args)[
            :256
        ],  # 인자 전체 JSON을 256자에서 절단 — 로그 비대화·민감정보 과다기록 방지
        "decision": decision,
        "trace_id": trace_id,  # OTel trace ID(32 hex) — 게이트웨이 로그·span과 동일 값이라 교차 추적 가능
    }
    try:
        # 부모 디렉터리가 없을 수 있으니 매 기록마다 보장(mkdir -p 동등). 비용 미미.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(
            path, "a", encoding="utf-8"
        ) as f:  # "a" = append 전용 — 기존 내용을 절대 덮지 않는다
            f.write(
                json.dumps(line) + "\n"
            )  # 줄당 JSON 1개(JSONL) — 부분 기록돼도 나머지 줄은 유효
    except OSError:
        # 디스크/권한 문제 등 → 호출은 이미 처리됐으니 막지 않고 에러만 남긴다(가용성 우선).
        logger.error("audit write failed (path=%s) — call proceeds", path)
