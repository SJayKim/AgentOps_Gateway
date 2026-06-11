# S6: (선택) LangGraph support-agent 데모 + audit admin 페이지 (Week 4)

> Epic: `00-epic.md` · 의존: S4 필수, S5 권장 · Effort: ~4-5일 · **선택 단계**

## Context

설계 문서 전제 4: "정책 거부 → 에이전트가 거부를 받고 우회 계획" 장면과 audit log가 가장 설득력 있는 산출물이다. 이 spec은 그 장면을 실제 LLM 추론으로 만들고, audit JSONL을 사람이 보는 페이지로 바꾼다.

**변경 가능 조건 (명시)**: 이 spec의 데모 시나리오는 The Assignment(첫 대상 회사와의 대화) 결과에 따라 교체될 수 있다. 대화에서 얻은 실제 상황이 있으면 시나리오를 그것으로 바꾸고 이 spec을 갱신한 뒤 착수한다. 또한 설계 Open Question 1 — LangGraph 데모와 admin 페이지 중 무엇을 먼저 할지 — 는 착수 시점에 결정한다 (둘은 독립적이라 순서 자유).

**유일한 실 LLM API 사용 지점**: Week 1-3은 외부 의존성 0이 제약이었다. 이 spec의 LangGraph 에이전트만 예외다.

## Current State

S4-S5 완료 시점 기준: 거부 payload `{"code": "POLICY_DENIED", "rule": "<agent>:<server>:<tool>", "agent": ...}` 계약 동작 중 (S4), E2E 스크립트가 스크립트 수준의 시나리오를 검증 (S5), audit JSONL 누적 중.

## Proposed Change

### Part A: LangGraph support-agent

- LangGraph로 support-agent를 구현. Gateway(:8000)에 support-agent JWT로 연결하는 MCP 클라이언트를 tool로 가짐.
- 데모 시나리오 (기본형 — The Assignment 결과로 교체 가능):
  1. 사용자 요청: "최근 결제 오류 관련 고객 문의를 정리하고, 서버 로그에서 원인을 찾아줘"
  2. 에이전트가 `ticket__search_tickets` → `docs__search_docs` 수행 (허용 — 성공)
  3. 에이전트가 `ops__query_logs` 시도 → `POLICY_DENIED` 수신
  4. **에이전트가 거부 payload를 파싱하고 우회 계획을 출력**: 예 — "ops 로그 접근 권한이 없습니다(rule: support-agent:ops:query_logs). 대안: ① dev-agent에 로그 조회를 요청 ② 문서의 알려진 오류 패턴으로 1차 분석 ③ 권한 승인 요청" 후 ②를 실제 수행
- 거부 처리 로직은 LLM 프롬프트에 박지 않고 그래프 노드로 구현 — `isError` + `code: POLICY_DENIED` 분기에서 우회 계획 노드로 라우팅. (LLM이 거부를 "오류"로 뭉개지 않게 구조로 보장.)
- LLM: Claude API (`claude-sonnet-4-6` 기본, env `ANTHROPIC_API_KEY`). 모델·키는 env로만 — 코드에 하드코딩 금지.
- 실행: `uv run python -m demo_agent` — 시나리오 전체를 stdout으로 내레이션.

### Part B: audit admin 페이지

- 질문 하나에 답하는 페이지: **"지난 24시간, 누가 민감 tool에 접근 시도했나"**
- Gateway에 `/admin` 라우트 (FastAPI + 서버사이드 렌더링, 프론트 프레임워크 없음 — Karpathy Simplicity):
  - 상단: 지난 24h 거부 건수 요약 (agent별 × server별 집계 테이블)
  - 본문: audit JSONL 역순 테이블 — ts, agent, tool, decision, args_summary, trace_id. decision=denied 행 강조
  - 필터: agent, decision, 시간 범위 (쿼리 파라미터, 폼 GET)
- 데이터 소스는 audit JSONL 파일 직접 읽기 (DB 도입 없음 — append-only 파일이 곧 진실).
- **리더 견고성 (eng review G2)**: 파일 미존재/빈 파일 → 빈 상태 렌더링 ("아직 기록 없음" — compose 재기동마다 깨끗한 상태로 시작하므로 이것은 엣지가 아니라 기본 경로다). 파싱 불가 줄(쓰기 중 프로세스 중단으로 절단된 마지막 줄 등) → 해당 줄 skip + 경고 로그, 페이지는 정상 렌더링.
- 접근 제어 (eng review T2): 데모 수준 — 최초 1회 `?token=` 으로 env `ADMIN_TOKEN` 검증 후 쿠키 설정, 이후 요청은 쿠키 검증. 쿼리 파라미터 토큰은 브라우저 히스토리·로그·README GIF에 박제되므로 상시 사용하지 않는다. 잘못된/부재 쿠키는 403. (실서비스 인증은 Out of Scope, README에 데모 한정임을 명시.)

### Implementation Details

- 새 패키지 `demo-agent/` 를 workspace member로 추가 (`pyproject.toml`: `langgraph`, `langchain-anthropic`, `mcp`). Gateway 의존성에 LLM 라이브러리를 섞지 않는다.
- admin은 `gateway/src/gateway/admin.py` + `templates/admin.html` (jinja2).
- compose에 demo-agent는 넣지 않는다 — API 키가 필요한 수동 실행 데모. README에 실행법 기록.
- README에 데모 GIF(정책 거부 → 우회 계획 장면) 추가 — Distribution Plan의 핵심 산출물.

## Acceptance Criteria

1. demo_agent 실행 시 시나리오 4단계가 완주되고, 3단계에서 `POLICY_DENIED` 의 `rule` 값을 출력에 포함
2. 우회 계획 출력이 거부 payload의 `rule` 을 참조하고(파싱 증명), 대안 중 하나(docs 검색)를 실제 수행해 결과를 보고
3. 거부 분기가 그래프 노드로 존재 — `POLICY_DENIED` 모의 응답을 주입한 단위 테스트에서 우회 노드로 라우팅됨을 LLM 호출 없이 검증
4. `/admin?token=...` 최초 접근이 쿠키를 설정하고 지난 24h 거부 요약 테이블 + 로그 테이블을 렌더링, 이후 쿠키만으로 접근 성공, 잘못된 토큰/쿠키는 403
5. admin 필터 3종(agent/decision/시간 범위)이 동작 — 거부만 필터 시 allowed 행 미표시
5b. audit 파일 미존재/빈 파일 → 빈 상태 렌더링 (비 500), 손상 줄 포함 파일 → 해당 줄 제외하고 정상 렌더링 + 경고 로그
6. demo_agent 실행 후 admin 페이지에 해당 거부 시도가 나타난다 (end-to-end 연결 증명)
7. 기존 전체 테스트 그린 유지 — demo_agent 의존성 추가가 gateway 테스트에 영향 없음

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | 거부 분기 노드 라우팅 (LLM 모킹), audit 24h 집계 함수 | +4 |
| Unit | audit 리더 견고성 — 빈 파일/손상 줄 (AC 5b) | +2 |
| Integration | admin 렌더링 + 필터 + 쿠키 인증 + 403 | +4 |
| Manual | demo_agent 실 LLM 완주 (AC 1, 2, 6 — CI 제외, 체크리스트로 기록) | 1회 |

LLM 호출 테스트는 CI에 넣지 않는다 (외부 의존·비용·비결정성). AC 1·2·6은 수동 검증 체크리스트로 README에 포함.

## Rollback Plan

demo-agent는 독립 패키지 — 제거해도 S1-S5 무손상. admin 라우트는 단일 모듈 — revert로 복구. API 키 노출 사고 시: 키 폐기·재발급 (코드에 키가 없으므로 레포 조치 불요).

## Effort Estimate

LangGraph 에이전트(거부 분기 그래프 포함) 2일 + admin 페이지 1일 + 통합·GIF·README 1일 ≈ 4일

## Files Reference

| File | Change |
|---|---|
| `demo-agent/pyproject.toml`, `demo-agent/src/demo_agent/{__main__,graph,mcp_client}.py` | 신규 |
| `pyproject.toml` | workspace member 추가 |
| `gateway/src/gateway/admin.py`, `gateway/templates/admin.html` | 신규 |
| `gateway/pyproject.toml` | `jinja2` 추가 |
| `tests/integration/test_admin.py` | 신규 |
| `README.md` | 섹션 추가만 — 데모 GIF, LangGraph 실행법, 수동 체크리스트 (기본판은 S5 산출) |

## Out of Scope

- admin 실서비스 인증/RBAC, audit 검색 인덱싱(파일 직독으로 충분한 규모)
- 멀티 에이전트 데모 (support-agent 1종만)
- 우회 계획의 자동 실행 승인 플로우 — 출력까지만

## Related

- Epic `00-epic.md` · 선행: S4 (거부 payload 계약), S5 권장 (trace ID로 admin-Grafana 교차 확인 가능)
- The Assignment 결과 반영 시 이 spec 갱신 후 착수
