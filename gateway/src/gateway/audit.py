"""audit log — append-only JSONL. 수정·삭제 코드 경로 없음.

decision enum은 allowed|denied|auth_failed|error 4종 — S5 메트릭과 동일
(eng review 이슈 2: 두 시스템이 같은 사실을 보게 한다).
기록 실패는 호출을 막지 않되 에러 로그 (가용성 > 감사 완결성, 데모 기준).
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def record(path: str, *, agent: str, tool: str, args: dict, decision: str) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "tool": tool,
        "args_summary": json.dumps(args)[:256],
        "decision": decision,
        "trace_id": str(uuid.uuid4()),  # S5에서 실제 trace로 대체
    }
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        logger.error("audit write failed (path=%s) — call proceeds", path)
