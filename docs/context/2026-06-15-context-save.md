---
status: in-progress
branch: main
timestamp: 2026-06-15T00:00:00+09:00
files_modified:
  - docs/context/2026-06-15-context-save.md (신규 — 본 상태 기록)
  - .gitignore (.gstack/ 제외 추가)
---

## Working on: AgentOps Gateway — S1–S6 + S5 stretch(P5) 완료, demo-agent 실 LLM 검증 완료

### Summary

2026-06-14 context-save 이후의 작업을 반영한다. 그 시점에 "Remaining Work"로 남았던
두 항목(circuit breaker stretch, demo-agent 실 LLM 완주)이 모두 마감됐고, rate limiting과
OPA/Rego 스파이크가 추가됐다. 이번 세션(/qa)에서 전체 테스트 그린과 docker compose 풀스택
e2e를 실측 검증했다. 핵심 데모 시스템은 안정 상태이며, 남은 일은 코드 밖(The Assignment,
TODOS 2건)뿐이다.

### Done since 2026-06-14

- **클라이언트별 rate limiting (token bucket)** — `bb75e64`
- **백엔드별 circuit breaker** — `8757f92` (2026-06-14 시점 "DoD 8 미구현 stretch"였던 항목 마감)
- **OPA/Rego 스파이크 메모** — `5a80344` (P5 stretch 조사)
- **demo-agent 실 LLM e2e 검증 완료** — `0ab731d` (DoD 7 / AC1·2·6, README 수동 검증 체크리스트 체크).
  2026-06-14 시점 `ANTHROPIC_API_KEY` 부재로 미실행이던 유일한 검증 공백이 닫혔다.
- **CLAUDE.md Gotcha 2건 룰 승격** — `76b950e`

### This session (2026-06-15, /qa 검증)

- **전체 97 테스트 그린** (`uv run pytest`, ~113s). 2026-06-14 77개 → 신규 stretch 테스트 포함 97개.
- **docker compose 풀스택 기동 + e2e smoke**: gateway·3 백엔드 healthy, Prometheus가 gateway 타겟
  UP로 스크레이프, Grafana `agentops-gateway` 대시보드 3패널 렌더.
- **메트릭 파이프라인 실측**: `scripts/e2e_demo.py`로 트래픽 발생(2 allowed + 1 POLICY_DENIED)
  → `/metrics` 카운터 → Prometheus → Grafana까지 end-to-end 확인.
- **코드/제품 버그 0건.** 초기 pytest 5개 실패는 직전 중단된 런이 남긴 orphan 백엔드
  프로세스(`docs_server`:8102, `ops_server`:8103)가 포트를 잡고 있던 환경 이슈였고, 프로세스
  정리 후 그린. 제품 코드 무변경.

### Findings (미수정 — 코드 밖/범위 밖)

- **`scripts/check_gateway.py` stale**: docstring은 "인증 없이 호출"이라지만 현재 gateway는
  미인증 호출에 401을 반환한다. gateway 동작이 옳고 스크립트 가정이 낡음. 토큰 추가(`e2e_demo.py`
  방식) 또는 docstring 정정 권장.

### Remaining Work

1. **The Assignment** — 첫 대상 회사 대화. S6 시나리오를 실제 상황으로 교체하는 유일한 입력.
2. **TODOS.md 2건 (코드 외, 솔로라 선택)**: ① The Assignment kill criterion 1–3줄 작성
   ② specs를 GitHub 이슈로 등록 (`gh auth login` 후).
3. (선택) `scripts/check_gateway.py` stale 정리.
