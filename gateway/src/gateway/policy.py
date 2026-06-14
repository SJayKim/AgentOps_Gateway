"""YAML 정책 엔진 — default-deny. 기동 시 1회 로드, 핫 리로드 없음.

[이 모듈이 프로젝트의 핵심]
권한 매트릭스(3 에이전트 × 3 서버) enforcement가 이 프로젝트의 존재 이유다
(design.md 전제 3: "Gateway가 없으면 이 매트릭스를 강제할 방법이 없다"). 그 강제를
실제로 수행하는 곳이 여기다.

[default-deny — 왜 화이트리스트인가]
YAML에 명시적으로 허용된 (에이전트, 서버, tool) 조합만 통과시키고, 나머지는 전부 거부한다.
블랙리스트(금지 목록)였다면 새 tool이 생길 때마다 "이건 막아야 하나?"를 사람이 챙겨야
하고 빠뜨리면 곧 사고다. 화이트리스트는 빠뜨리면 '거부'(안전한 실패)로 떨어진다.

[evaluate가 절대 예외를 던지지 않는 이유 — eng review T3]
정책 계층은 공격적/기형적 입력을 시스템에서 가장 먼저 만나는 곳이다. 여기서 예외가 나면
처리 경로가 깨지며 fail-open(우연한 허용)이 될 위험이 있다. 그래서 어떤 입력에도 예외
대신 항상 Decision을 반환하고, 의심스러우면 거부(fail-closed)한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """정책 평가 결과. 허용 여부 + 사람이 읽을 규칙 식별자 + (선택) 거부 상세."""

    allowed: bool
    rule: str  # "<agent>:<server>:<tool>" — POLICY_DENIED payload의 rule 필드 계약(에이전트가 파싱)
    detail: str | None = None  # 인자 레벨 위반 등 추가 설명 (예: 시간 범위 초과 사유)


class Policy:
    def __init__(self, agents: dict):
        # 구조: agent_id -> { server -> [ tool 문자열 | {"tool": ..., 제약필드} ] }
        # 문자열 항목 = 제약 없는 단순 허용, dict 항목 = 인자 레벨 제약이 붙은 허용.
        self.agents = agents

    @classmethod
    def load(cls, path: str) -> "Policy":
        """YAML 파일에서 정책을 읽어 인스턴스화. 기동 시 1회만 호출된다."""
        with open(path, encoding="utf-8") as f:
            # safe_load: 임의 파이썬 객체 역직렬화를 막는다(정책 파일은 신뢰 경계 밖일 수 있음).
            # 빈 파일이면 None이 나오므로 {}로 정규화 → 모든 조합이 default-deny.
            return cls(yaml.safe_load(f) or {})

    def warn_unknown_tools(self, known: dict[str, set[str]]) -> None:
        """YAML에 적힌 tool 이름을 실제 집계된 백엔드 tool과 대조해 오타를 경고한다.

        [왜 필요한가 — eng review 이슈 5]
        default-deny에서는 정책 YAML의 tool 이름 오타가 에러로 드러나지 않는다. 오타 난
        규칙은 그냥 매칭이 안 돼 '조용한 거부'가 되고, 운영자는 "왜 허용한 tool이 막히지?"로
        한참 헤맨다. 그래서 기동 시 한 번 대조해 미존재 서버/tool을 로그로 가시화한다.

        known에 없는(기동 시 다운된) 백엔드는 검증할 방법이 없으니 건너뛴다 — 경고만, 에러 아님.
        """
        for agent_id, servers in self.agents.items():
            for server, entries in (servers or {}).items():
                if server not in known:
                    logger.warning("policy: %s references unknown server %r", agent_id, server)
                    continue
                for entry in entries or []:
                    # 문자열이면 그 자체가 tool 이름, dict면 "tool" 키에서 꺼낸다.
                    tool = entry["tool"] if isinstance(entry, dict) else entry
                    if tool not in known[server]:
                        logger.warning(
                            "policy: %s references unknown tool %r on server %r",
                            agent_id,
                            tool,
                            server,
                        )

    def evaluate(self, agent_id: str, server: str, tool: str, args: dict) -> Decision:
        """(agent, server, tool, args)가 정책상 허용되는지 판정. 항상 Decision 반환(예외 없음)."""
        rule = f"{agent_id}:{server}:{tool}"  # 거부 응답에 그대로 실릴 식별자를 먼저 만든다
        # 이 에이전트의 이 서버에 대한 허용 항목 목록. 없으면(미기재) None → 아래 루프 건너뛰고 거부.
        entries = (self.agents.get(agent_id) or {}).get(server)
        for entry in entries or []:
            if isinstance(entry, dict):
                # 제약 붙은 항목: tool 이름이 맞으면 인자 레벨 검사로 넘긴다.
                if entry.get("tool") == tool:
                    return self._check_args(rule, entry, args)
            elif entry == tool:
                # 단순 문자열 항목과 일치 → 제약 없이 허용.
                return Decision(allowed=True, rule=rule)
        # 어느 항목과도 안 맞음 = 미기재 = default-deny.
        return Decision(allowed=False, rule=rule)

    def _check_args(self, rule: str, entry: dict, args: dict) -> Decision:
        """인자 레벨 정책 검사. 현재 지원하는 제약은 max_range_hours 하나.

        [구체 사례 — design.md "인자 레벨 정책"]
        analyst-agent의 query_logs는 조회 시간 범위가 최대 24시간으로 제한된다. 위반 시
        값을 잘라 맞추는(clamping) 게 아니라 '거부'한다 — 이 데모의 목적은 정책 위반을
        '가시화'하는 것이라, 조용히 보정하면 보여줄 거부 장면이 사라지기 때문이다.
        """
        max_hours = entry.get("max_range_hours")
        if max_hours is None:
            # tool 이름은 허용 목록에 있고 시간 제약은 없는 경우 → 그냥 허용.
            return Decision(allowed=True, rule=rule)
        try:
            start = datetime.fromisoformat(args["start"])
            end = datetime.fromisoformat(args["end"])
            delta = end - start  # naive와 aware datetime을 섞으면 TypeError → 아래서 거부 처리
        except (KeyError, TypeError, ValueError) as e:
            # start/end 누락·형식 오류·tz 혼용 → 판정 불가이므로 안전하게 거부(fail-closed).
            return Decision(allowed=False, rule=rule, detail=f"invalid time range: {e}")
        if delta.total_seconds() < 0:
            # end가 start보다 앞 → 무의미한 범위. 거부하고 사유를 detail로 알린다.
            return Decision(allowed=False, rule=rule, detail="time range end before start")
        hours = delta.total_seconds() / 3600
        if hours > max_hours:
            return Decision(
                allowed=False, rule=rule, detail=f"time range {hours:g}h exceeds max {max_hours}h"
            )
        return Decision(allowed=True, rule=rule)
