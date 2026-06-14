---
status: in-progress
branch: main
timestamp: 2026-06-14T00:00:00+09:00
files_modified:
  - demo-agent/ (신규 패키지)
  - gateway/src/gateway/admin.py (신규)
  - gateway/src/gateway/templates/admin.html (신규)
  - tests/unit/test_admin_reader.py, tests/unit/test_demo_agent.py (신규)
  - tests/integration/test_admin.py (신규)
  - README.md, docker-compose.yml, pyproject.toml (갱신)
---

## Working on: AgentOps Gateway — S1–S6 구현 완료, demo-agent 실 LLM 완주만 미검증

### Summary

4주 로드맵(Epic S1–S6)을 전부 구현해 main에 커밋했다. 2026-06-10 context-save 시점엔
코드 0줄이었고, 이후 S1–S5(scaffold→backend→gateway core→auth/policy/audit→observability)에
이어 이번 세션에 **S6(선택 단계)** 까지 마쳤다. 핵심 데모 시스템(`docker compose up` 원커맨드
기동, 9칸 권한 매트릭스 enforcement, audit·메트릭·trace)은 S5에서 완성됐고, S6가 그 위에
LangGraph 우회 데모와 audit admin 페이지를 얹었다.

### Decisions Made (이번 세션, S6)

- **착수 시점 결정 2건 확정** (spec 06 상단 명시 항목):
  - 시나리오: The Assignment(첫 대상 회사 대화)가 아직 없고 설계상 Demand Evidence=0이므로
    spec의 **기본 시나리오** 사용 (결제 오류 문의 → ops 로그 거부 → 우회)
  - 순서: **Part B(admin) 먼저** — LLM·API 키 없이 CI로 완전 검증 가능. 이후 Part A
- **거부 분기를 그래프 노드로 구현** (`demo_agent.graph.route_after_tools`) — LLM 프롬프트에
  박지 않아, LLM이 POLICY_DENIED를 generic 오류로 뭉개는 실패 모드를 구조로 차단. AC3을
  LLM 호출 없이 단위 테스트로 검증
- **demo-agent를 dev 그룹에만** 배치 — 테스트는 graph를 import하되 gateway 프로덕션
  이미지(`uv sync --no-dev --package agentops-gateway`)엔 langgraph가 섞이지 않는다
- **admin 템플릿을 패키지 내부**(`src/gateway/templates/`)로 이동 — spec의 `gateway/templates/`
  에서 의도적 변경. wheel/editable/소스 실행 어느 경로든 `__file__` 기준으로 찾히게
- admin 인증은 데모 수준 쿠키(`ADMIN_TOKEN`, 상수시간 비교). 실서비스 세션/RBAC는 Out of Scope

### Verification

- 전체 **77 테스트 그린** (S6 신규 17: admin reader 5 · admin route 6 · demo-agent 분기 6)
- ruff lint + format 통과. `build_graph` 컴파일·`__main__` import 확인
- 커밋: `e4ab7fa feat(S6): LangGraph 우회 데모 + audit admin 페이지`
  (메시지에 "77+17"로 오기 — 정확히는 총 77개 중 신규 17개)

### Remaining Work

1. **demo-agent 실 LLM 1회 완주 (DoD 7, AC1·2·6)** — `ANTHROPIC_API_KEY`가 없어 미실행.
   README에 수동 검증 체크리스트로 등록됨. 키 확보 시 실행해 마감.
2. **DoD 8 (circuit breaker stretch)** — 미구현. 의도적 stretch, 범위 밖.
3. **TODOS.md 2건 (코드 외, 솔로라 선택)**: ① The Assignment kill criterion 1–3줄 작성
   ② specs를 GitHub 이슈로 등록 (`gh auth login` 후)
4. **The Assignment** — 첫 대상 회사 대화. S6 시나리오를 실제 상황으로 교체하는 유일한 입력.
   결과에 따라 spec 06 갱신 후 demo-agent 시나리오 교체 가능.
