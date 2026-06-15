---
status: in-progress
branch: main
timestamp: 2026-06-12T11:56:41+09:00
files_modified: []
---

## Working on: S5 출하 완료 — CI 그린(test+e2e), S6 선택 단계 진입 가능

### Summary

S5(관측성) 커밋 `9a890fb`를 origin/main에 push 완료(사용자 승인), CI run `27391462445` **success — test·e2e 잡 둘 다 그린**. 워킹 트리 클린, main == origin/main. S1-S5 전부 출하. 신규 CI e2e 잡이 GitHub 러너에서 `docker compose up --wait` 후 성공-성공-거부 시나리오를 검증하므로 도커 레벨 검증도 완료(직전 체크포인트의 "S4 도커 검증 미실시" 해소).

### Decisions Made

- 메트릭 기록 지점은 audit 기록 지점과 동일한 단일 지점(app.py call_tool) — 두 시스템이 같은 사실을 보게 (spec 명시)
- OTel exporter는 SimpleSpanProcessor+Console — BatchSpanProcessor는 atexit flush가 pytest가 닫은 stdout에 써서 ValueError. 동기식이 데모 규모에 단순·충분
- trace "전파"는 게이트웨이 내 span 계층 + audit/로그 trace_id 일치(AC2)로 충족 — MCP 세션은 호출별 헤더 주입 불가라 cross-service 헤더 전파는 범위 밖
- 백엔드 healthcheck는 FastMCP `@mcp.custom_route("/health")` + 컨테이너 내 `/app/.venv/bin/python -c urllib` 폴링 (slim 이미지에 curl 없음)
- duration histogram은 route_call 전체를 관측(denied 포함) — 단순성 우선
- e2e_demo.py stdout은 ASCII 고정 — Windows cp949 콘솔에서 em-dash가 UnicodeEncodeError
- 통합 테스트 파일명은 unit과 중복 금지 (`test_observability.py` 2개 → import file mismatch) — 통합 쪽을 `test_metrics_tracing.py`로 명명

### Remaining Work

1. **S6 착수 (선택)**: `docs/specs/06-langgraph-admin.md` — LangGraph 에이전트·데모 GIF·README 섹션 추가
2. **S6 데모 리허설 전 포트 확보**: 8000·3000을 catchment-area-analysis 컨테이너가 점유 중 — compose 기동 후 Grafana 패널 실데이터(AC3)·Prometheus 쿼리(AC4) 육안 확인 필요 (provisioning 코드로만 검증된 상태)
3. **The Assignment** (이월): 회사 담당자 첫 대화 전 TODOS.md에 kill criterion 작성
4. **CLAUDE.md Gotcha 제안 2건 사용자 확인 대기**: ① mcp 1.27 bare dict → structuredContent 없음 ② 프로세스 내 uvicorn 2회 기동 테스트는 sse-starlette AppStatus 리셋 필요
5. S5 stretch(P5)는 미착수 — rate limiting > circuit breaker > OPA 스파이크 순, 별도 커밋으로만

### Notes

- 전체 테스트 60개 / ~116초. 행 걸리면 8101-8103 고아 프로세스 정리 (`Get-NetTCPConnection`)
- 로컬 E2E 리허설 패턴: 백엔드 3종 + 게이트웨이를 포트 8200으로 띄우고 `GATEWAY_URL=http://127.0.0.1:8200/mcp uv run python scripts/e2e_demo.py` (포트 8000 점유 회피)
- CI e2e secret은 compose 데모값 `demo-secret-do-not-use-in-prod`와 일치해야 함
- GitHub Actions 경고: actions/checkout@v4·setup-uv@v5가 Node 20 deprecated — 2026-09-16 러너 제거 전 버전 업 필요 (지금은 그린)
- Grafana는 익명 Admin(데모용), 대시보드 uid `agentops-gateway`, datasource uid `prometheus`
