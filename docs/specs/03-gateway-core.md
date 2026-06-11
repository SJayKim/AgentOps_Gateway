# S3: Gateway 코어 — aggregation · 라우팅 · 세션 유지 (Week 1)

> Epic: `00-epic.md` · 의존: S2 · Effort: ~2-3일

## Context

프로젝트의 중심 컴포넌트. 에이전트는 백엔드 3종이 아니라 Gateway 하나에만 연결하고, Gateway가 tool 목록 집계와 호출 라우팅을 담당한다. 이 spec은 설계 문서 Week 1 범위 — 인증·정책은 다음 spec(S4)이 이 위에 얹는다.

## Current State

S2 완료 시점 기준 백엔드 3종이 :8101-8103에서 단독 동작. `gateway/` 패키지는 빈 상태.

## Proposed Change

FastAPI 기반 Gateway(:8000)가 MCP Streamable HTTP 서버로 동작하며, 다음 3가지를 구현한다.

### 1. tools/list aggregation — prefix 네임스페이싱

- 기동 시 백엔드 3종에 각각 `tools/list` 를 호출해 집계하고, 클라이언트에는 prefix가 붙은 단일 목록을 반환:
  `ticket__create_ticket`, `ticket__search_tickets`, `ticket__update_status`, `docs__search_docs`, `docs__read_doc`, `ops__get_metrics`, `ops__query_logs` — **총 7개**
- prefix 구분자는 더블 언더스코어 `__` 고정 (tool 이름에 등장할 수 없는 시퀀스로 예약).
- **tools/list는 정책으로 필터링하지 않는다** — 모든 클라이언트가 7개 전체를 본다. 의도적 설계: support-agent가 ops tool의 존재를 알아야 S6의 "호출 시도 → 거부" 장면이 자연스럽다. 코드 주석으로 이 의도를 남기고, README(S5)에 "production에서는 정책 기반 목록 필터링이 기본값이어야 한다"는 대안 한 줄을 명시한다 (eng review T6 — 알고 선택한 데모 설계임을 기록).

### 2. 라우팅

- `tools/call` 의 tool 이름에서 prefix를 떼어 해당 백엔드로 전달하고, 응답을 그대로 중계한다.
- prefix가 없거나 미등록 prefix(`unknown__x`)거나 백엔드에 없는 tool이면 MCP `isError: true` + `{"code": "UNKNOWN_TOOL", "tool": "<요청된 이름>"}`. (거부 응답 구조화 형식은 S4의 `POLICY_DENIED`와 같은 패턴을 선취한다.)

### 3. 백엔드 세션 유지

- **백엔드당 MCP 세션 1개를 열어 재사용한다. 커넥션 풀이 아니다.** 동시 호출은 세션을 공유한다.
- 끊김 감지 시 재연결은 백엔드별 `asyncio.Lock` 뒤에서 직렬화 — 동시 재연결 시도로 세션이 중복 생성되는 것을 방지.
- 재연결 1회 재시도 후에도 실패하면 해당 호출은 `isError: true` + `{"code": "BACKEND_UNAVAILABLE", "server": "<이름>"}`. (백엔드 다운 시 tools/list 제외는 S5 stretch의 circuit breaker — 여기서는 하지 않는다.)
- 기동 시 백엔드 일부가 죽어 있으면: 살아있는 백엔드만 집계하고 경고 로그. Gateway 기동 자체는 실패하지 않는다 (compose 기동 순서 의존 제거).
- **지연 재집계 (eng review T1)**: prefix 등록(= env var 설정)과 tool 목록 집계를 분리한다. 등록된 서버인데 tool이 미집계 상태면, 해당 prefix로의 tools/call 또는 tools/list 시점에 재연결 + 재집계를 시도한다. 이것이 없으면 기동 시 죽어 있던 백엔드의 tool이 목록에 없어 호출이 `UNKNOWN_TOOL`로 거부되고, "첫 호출 시 재연결" 기회가 영원히 오지 않는 데드락이 된다.

### Implementation Details

- `gateway/src/gateway/` 구성: `__main__.py`(uvicorn 실행), `app.py`(FastAPI + MCP Streamable HTTP 마운트), `upstream.py`(백엔드 세션 관리 — 세션 1개/lock/재연결), `aggregate.py`(목록 집계 + prefix), `routes.py`(tools/call 라우팅), `errors.py`(구조화 오류 결과 헬퍼 — 아래 참조).
- **errors.py (eng review 이슈 4)**: `isError: true` + `{"code": ..., ...}` payload 생성은 이 헬퍼 하나로만 한다. `UNKNOWN_TOOL`/`BACKEND_UNAVAILABLE`(S3), `POLICY_DENIED`/`AUTH_FAILED`(S4), `RATE_LIMITED`(S5 stretch) 전부 이 함수를 재사용 — payload 스키마는 단위 테스트로 1회 고정. S6가 파싱하는 계약의 단일 진실 지점.
- **Day 1 동시성 스파이크 (eng review 이슈 3)**: 구현 첫날, `streamablehttp_client` + `ClientSession` 1개 위에서 동시 10호출(asyncio.gather)이 안전한지 최소 스크립트로 먼저 검증한다. 세션 공유 설계 전체가 이 가정 위에 있으므로, 가정이 틀리면 이 시점에 설계를 수정한다. 스파이크 코드는 AC 5 통합 테스트의 기초로 재사용.
- 백엔드 주소는 env var: `BACKEND_TICKET_URL`, `BACKEND_DOCS_URL`, `BACKEND_OPS_URL` (compose에서 서비스명으로 주입, 로컬 기본값 `http://localhost:810{1,2,3}`).
- 의존성 (`gateway/pyproject.toml`): `mcp`, `fastapi`, `uvicorn`, `httpx`.
- Gateway Dockerfile 작성, compose의 `gateway` 서비스 채움 (`depends_on` 백엔드 3종 — 단, 위 기동 정책 덕에 hard 의존은 아님).
- S4를 위한 자리: 요청 처리 경로를 "인증 → 정책 → 라우팅" 순으로 끼워 넣을 수 있는 단일 함수 경로로 유지. 미리 미들웨어 추상화를 만들지는 않는다 (Karpathy: 요청 안 한 추상화 금지).

## Acceptance Criteria

1. Gateway 경유 `tools/list` 가 prefix 붙은 tool 정확히 7개를 반환한다 (이름 완전 일치 검증)
2. `ticket__create_ticket` 호출이 Gateway 경유로 성공하고, 백엔드 직접 호출과 동일한 응답 반환
3. 3개 백엔드 각각 대표 tool 1개씩 Gateway 경유 호출 성공
4. `unknown__x`, prefix 없는 `create_ticket`, `ticket__nonexistent` 호출이 전부 `isError: true` + `code: UNKNOWN_TOOL`
5. 동시 호출 10개(asyncio.gather, 같은 백엔드)가 전부 성공 — 세션 공유 검증
6. 백엔드 1종 프로세스 kill 후 호출 → `BACKEND_UNAVAILABLE`, 백엔드 재기동 후 다음 호출 → 자동 재연결로 성공
7. 백엔드 1종이 죽은 상태에서 Gateway 기동 → 기동 성공 + 나머지 tool들 정상 동작, **해당 백엔드 재기동 후 그 백엔드의 tool이 tools/list에 나타나고 호출도 성공** (지연 재집계 검증)
8. `docker compose up` 으로 4서비스(gateway + 백엔드 3종) 동시 기동 후 AC 1-3 통과

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | prefix 파싱/조립, UNKNOWN_TOOL 분기 | +3 |
| Unit | errors.py payload 스키마 (code별 필드 정확성) | +1 |
| Integration | AC 1-7 (실제 백엔드 기동 상태에서) | +7 |

## Rollback Plan

신규 코드만 — revert로 복구. 백엔드(S2)는 변경하지 않으므로 영향 없음.

## Effort Estimate

aggregation+라우팅 1일 + 세션 관리(재연결·lock) 0.5일 + 장애 시나리오 테스트 0.5일 + Docker 통합 0.5일 ≈ 2.5일

## Files Reference

| File | Change |
|---|---|
| `gateway/src/gateway/{__main__,app,upstream,aggregate,routes,errors}.py` | 신규 |
| `gateway/Dockerfile` | 신규 |
| `gateway/pyproject.toml` | 의존성 추가 |
| `docker-compose.yml` | gateway 서비스 채움 |
| `tests/integration/test_gateway_core.py` | 신규 |

## Out of Scope

- 인증·정책·audit (S4), trace ID·메트릭 (S5), circuit breaker (S5 stretch)
- tools/list 캐시 무효화 전략 (백엔드 tool 목록은 이 프로젝트에서 정적)

## Related

- Epic `00-epic.md` · 선행: S2 · 다음: S4 `04-auth-policy-audit.md`
