"""YAML 정책 엔진 — default-deny. 기동 시 1회 로드, 핫 리로드 없음.

evaluate는 어떤 입력에도 예외를 내지 않고 결정을 내린다 (eng review T3) —
정책 계층은 공격적 입력을 가장 먼저 만나는 곳이고, 거부가 기본값이다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    allowed: bool
    rule: str  # "<agent>:<server>:<tool>" — POLICY_DENIED payload의 rule 필드 계약
    detail: str | None = None


class Policy:
    def __init__(self, agents: dict):
        self.agents = agents  # agent_id -> {server -> [tool | {"tool": ..., 제약}]}

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    def warn_unknown_tools(self, known: dict[str, set[str]]) -> None:
        """YAML의 tool 이름을 집계된 백엔드 tool 목록과 대조 — 미존재는 경고.

        default-deny에서 오타는 에러가 아니라 조용한 거부가 되므로 (eng review
        이슈 5). 미집계 백엔드(기동 시 다운)는 검증 불가라 건너뛴다 — warn only.
        """
        for agent_id, servers in self.agents.items():
            for server, entries in (servers or {}).items():
                if server not in known:
                    logger.warning("policy: %s references unknown server %r", agent_id, server)
                    continue
                for entry in entries or []:
                    tool = entry["tool"] if isinstance(entry, dict) else entry
                    if tool not in known[server]:
                        logger.warning(
                            "policy: %s references unknown tool %r on server %r",
                            agent_id,
                            tool,
                            server,
                        )

    def evaluate(self, agent_id: str, server: str, tool: str, args: dict) -> Decision:
        rule = f"{agent_id}:{server}:{tool}"
        entries = (self.agents.get(agent_id) or {}).get(server)
        for entry in entries or []:
            if isinstance(entry, dict):
                if entry.get("tool") == tool:
                    return self._check_args(rule, entry, args)
            elif entry == tool:
                return Decision(allowed=True, rule=rule)
        return Decision(allowed=False, rule=rule)  # default-deny: 미기재 = 거부

    def _check_args(self, rule: str, entry: dict, args: dict) -> Decision:
        max_hours = entry.get("max_range_hours")
        if max_hours is None:
            return Decision(allowed=True, rule=rule)
        try:
            start = datetime.fromisoformat(args["start"])
            end = datetime.fromisoformat(args["end"])
            delta = end - start  # naive/aware 혼용은 TypeError → 거부
        except (KeyError, TypeError, ValueError) as e:
            return Decision(allowed=False, rule=rule, detail=f"invalid time range: {e}")
        if delta.total_seconds() < 0:
            return Decision(allowed=False, rule=rule, detail="time range end before start")
        hours = delta.total_seconds() / 3600
        if hours > max_hours:
            return Decision(
                allowed=False, rule=rule, detail=f"time range {hours:g}h exceeds max {max_hours}h"
            )
        return Decision(allowed=True, rule=rule)
