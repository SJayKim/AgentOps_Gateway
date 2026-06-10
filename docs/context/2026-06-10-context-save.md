---
status: in-progress
branch: main
timestamp: 2026-06-10T16:54:53+09:00
files_modified:
  - .claude/settings.json
  - .claude/hooks/protect-files.sh
  - .gitignore
---

## Working on: AgentOps Gateway — 환경 세팅 + 설계 승인 완료, 구현 착수 전

### Summary

빈 레포에서 Claude Code 환경 세팅(CLAUDE.md + Karpathy 4원칙, 보안 hook, 스킬 라우팅)을 마치고, /office-hours 세션으로 설계 문서를 작성·적대적 리뷰 3라운드(17개 이슈 수정, 최종 9/10 PASS)·사용자 승인까지 완료했다. 코드는 아직 한 줄도 없다. 다음 단계는 Week 1 구현이다.

### Decisions Made

- **Approach B (원안 풀 플랜 4주) 채택** — AI 권고는 C(Governance Wedge)였으나 사용자가 관측성 스택(OTel/Prometheus/Grafana)이 본인 커리어 축이라는 이유로 B 선택. 승인된 설계 문서: `docs/design/agentops-gateway-design.md` (repo) = `~/.gstack/projects/SJayKim-AgentOps_Gateway/user-main-design-20260610-164650.md` (gstack)
- **전제 1 수정·합의**: 이 4주 산출물은 데모 시스템. 실서비스 경로는 첫 대상 회사와의 대화(별도 단계). 수요 증거는 현재 0 — "특정 회사 염두에 있으나 아직 대화 전"
- 핵심 구현 명세 확정: default-deny YAML 정책, JWT HS256 정적 사전발급, 거부 응답 `isError + {code: POLICY_DENIED, rule: agent:server:tool}`, audit JSONL은 Week 2 포함, tools/list는 정책 필터링 안 함(데모를 위한 의도적 선택), rate limit/circuit breaker/OPA는 stretch
- 스타트업 모드로 진행됨 (gstack office-hours), cross_project_learnings=false, telemetry=community

### Remaining Work

1. **The Assignment (코드보다 먼저/병행)**: 염두에 둔 회사 담당자와 첫 대화 — "에이전트의 사내 도구 접근을 지금 어떻게 통제하나" 질문 하나
2. Week 1 구현: FastAPI Gateway 코어 — Streamable HTTP, tools/list aggregation(prefix 네임스페이싱), 라우팅, 백엔드 세션 유지 + 백엔드 MCP 서버 3종(ticket/docs/ops) 스캐폴드
3. Week 2: JWT + YAML 정책 엔진 + audit JSONL (설계 문서의 "구현 명세 결정사항" 그대로)
4. Week 3: OTel/Prometheus/Grafana (우선순위: 거부 카운트 메트릭 > 대시보드 > stretch 3종)
5. Week 4 (선택): LangGraph support-agent 데모 + audit admin 페이지
6. 구현 착수 시 /plan-eng-review 권장 (설계 문서를 자동으로 읽음)

### Notes

- office-hours 세션은 Phase 6(handoff/closing) 직전에 사용자 요청으로 중단됨 — 설계 승인(D10)까지는 완료된 상태라 기능적 손실 없음
- Week 3가 솔로 프로젝트 통상 이탈 지점 — Week 1-2 완결 전 Week 3 진입 금지, 시간 부족 시 Grafana 패널을 줄이되 정책 거부 카운트 메트릭은 절대 빼지 않기로 합의
- 보안 hook(.claude/hooks/protect-files.sh)은 jq 없이 sed로 동작하도록 수정됨 (이 머신 Git Bash에 jq 없음) — 다른 머신에서도 jq 불필요
- 성공 기준: 권한 매트릭스 9칸 통합 테스트, 스크립트 E2E(LLM 불필요, Week 3 기준), `docker compose up` 원커맨드 기동
