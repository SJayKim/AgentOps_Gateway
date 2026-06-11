# S4: JWT 인증 + YAML 정책 엔진 + audit log (Week 2)

> Epic: `00-epic.md` · 의존: S3 · Effort: ~4-5일

## Context

프로젝트의 존재 이유가 구현되는 spec. 권한 매트릭스 3×3을 tool call 단위로 강제하고, 모든 호출을 감사 기록한다. **이 spec의 9칸 매트릭스 통합 테스트가 통과하기 전에는 S5에 진입하지 않는다** (Epic 순서 규칙). 설계 문서의 "구현 명세 결정사항"이 이미 세부를 확정했으므로, 이 spec은 그것을 검증 가능한 형태로 옮긴 것이다 — 임의 변경 금지.

## Current State

S3 완료 시점 기준 Gateway가 인증 없이 모든 호출을 라우팅. 요청 경로는 "인증 → 정책 → 라우팅"을 끼울 수 있는 단일 함수 경로로 준비되어 있음 (S3 Implementation Details).

## Proposed Change

### 1. JWT 인증 (정적 사전발급)

- **발급 서버는 만들지 않는다.** `scripts/issue_tokens.py` 가 에이전트 3개분 토큰을 생성해 stdout으로 출력 (데모 사용자가 복사해 쓰는 형태 + 테스트 픽스처가 동일 함수 재사용).
- claim: `{"agent_id": "<support-agent|analyst-agent|dev-agent>", "exp": <30일 후>}`. 만료 30일은 데모 편의.
- 서명 HS256, secret은 env var `GATEWAY_JWT_SECRET` (compose에 데모값 주입, **레포에 실제 운영 secret을 두지 않음을 README에 명시**).
- 전송: `Authorization: Bearer <token>` 헤더. 라이브러리: `pyjwt`.
- 인증 실패 분기 (eng review 이슈 1 — 이중 계층):
  - **tools/call**: MCP `isError: true` + `{"code": "AUTH_FAILED", "reason": "<missing|invalid|expired>"}` (errors.py 헬퍼 사용, S6 파싱 계약 유지)
  - **비-tool-call 요청 (initialize, tools/list)**: HTTP 401 + JSON body `{"code": "AUTH_FAILED", "reason": ...}` — tool result는 tools/call 응답에만 존재하므로 transport 계층 표준 의미론을 따른다.

### 2. YAML 정책 엔진 — default-deny

- 정책 파일 `policies/policy.yaml` (Gateway 기동 시 1회 로드, 핫 리로드 없음):

```yaml
support-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
  # ops 미기재 = 전부 거부
analyst-agent:
  ticket: [search_tickets]
  docs: [search_docs, read_doc]
  ops: [get_metrics, query_logs]
dev-agent:
  ticket: [create_ticket, search_tickets, update_status]
  docs: [search_docs, read_doc]
  ops: [get_metrics, query_logs]
```

- **default-deny**: 매트릭스에 없는 (에이전트, tool) 조합은 전부 거부. 미등록 agent_id도 전부 거부.
- **로드 시 검증 (eng review 이슈 5)**: default-deny에서 YAML 오타는 에러가 아니라 조용한 거부가 된다. 정책 로드 시 YAML의 tool 이름을 집계된 백엔드 tool 목록과 대조해, 미존재 이름은 경고 로그를 남긴다 (기동 시 백엔드 다운 케이스가 있으므로 fail-fast가 아닌 warn).
- 위 YAML은 권한 매트릭스의 직역이다: "읽기"는 검색/조회 tool, "읽기+쓰기"는 전체 tool. analyst-agent의 ticket "읽기" = `search_tickets`만.

### 3. 인자 레벨 정책

- 단 1개: analyst-agent의 `ops__query_logs` 는 `end - start <= 24h`. **dev-agent에는 적용하지 않는다** (읽기+쓰기 권한 — 매트릭스 차등의 시연).
- 위반 시 클램핑이 아니라 **거부** — 정책 위반을 가시화하는 것이 목적.
- **이상 입력 처리 (eng review T3)**: 정책 평가는 어떤 입력에도 예외를 내지 않고 결정을 내린다. ① ISO8601 파싱 불가 ② `end < start` ③ naive/aware datetime 혼용으로 비교 불가 — 셋 다 `POLICY_DENIED` + `detail` (거부가 기본값, default-deny 철학 일관). 정책 계층은 공격적 입력을 가장 먼저 만나는 곳이며, S2 서버 측 검증은 정책 평가 뒤라 이 경로를 막아주지 못한다.
- 정책 표현: `policy.yaml`에 인라인:

```yaml
analyst-agent:
  ops:
    - get_metrics
    - tool: query_logs
      max_range_hours: 24
```

### 4. 거부 응답 형식 (고정 계약 — S6 데모의 전제)

```json
{"code": "POLICY_DENIED", "rule": "<agent>:<server>:<tool>", "agent": "<agent_id>"}
```

- MCP tool result `isError: true` + 위 구조화 payload. `rule` 은 YAML 매핑에서 그대로 도출되는 형식 (예: `support-agent:ops:query_logs`).
- 인자 레벨 위반도 동일 형식 + `"detail": "time range 26h exceeds max 24h"` 필드 추가.
- 에이전트가 파싱해 우회 계획을 세울 수 있어야 한다 — 이 형식은 S6와의 계약이므로 변경 시 S6 spec도 함께 수정.

### 5. audit log — append-only JSONL

- 모든 tools/call 에 대해 1줄 기록 (`audit/audit.jsonl`, 경로는 env `GATEWAY_AUDIT_PATH`):

```json
{"ts": "<ISO8601>", "agent": "<agent_id|anonymous>", "tool": "<prefixed name>", "args_summary": "<전체 인자 JSON의 256자 절단>", "decision": "<allowed|denied|auth_failed|error>", "trace_id": "<S5에서 채움, 그 전까지 uuid4>"}
```

- 허용/거부/인증실패/오류 전부 기록. `decision: error`는 `UNKNOWN_TOOL`·`BACKEND_UNAVAILABLE` 등 정책 외 실패 — S5 메트릭의 decision 4종과 동일 enum (eng review 이슈 2: 두 시스템이 같은 사실을 보게 한다는 원칙을 enum 수준에서 유지). append-only — 수정·삭제 코드 경로 없음.
- 기록 실패(디스크 등)는 호출을 막지 않되 에러 로그 (가용성 > 감사 완결성, 데모 시스템 기준).

### Implementation Details

- `gateway/src/gateway/` 에 추가: `auth.py`(JWT 검증), `policy.py`(YAML 로드 + 평가 — allowed(agent, server, tool, args) -> Decision), `audit.py`(JSONL writer). 거부/오류 payload 생성은 S3의 `errors.py` 헬퍼 재사용 (신규 생성 경로 금지).
- **평가 순서 (고정, eng review 이슈 2)**: ① 인증 → ② tool 해석 (prefix 분해, 미등록이면 `UNKNOWN_TOOL`) → ③ 정책 평가. 정책 평가는 서버·tool이 확정된 후에만 수행 — 미존재 tool은 `POLICY_DENIED`가 아니라 `UNKNOWN_TOOL`이다.
- `tools/list` 는 인증은 요구하되 정책 필터링 없음 (Epic Out of Scope 재확인).
- 의존성 추가: `pyjwt`, `pyyaml`.

## Acceptance Criteria

1. **9칸 매트릭스 통합 테스트** — 셀당 최소 1개 테스트, 총 11개 케이스:
   - 허용 8칸: support×ticket, support×docs, analyst×ticket, analyst×docs, analyst×ops, dev×ticket, dev×docs, dev×ops — 각 셀의 대표 허용 tool 1개 호출 성공
   - 금지 1칸: support×ops — `ops__query_logs` 호출이 `POLICY_DENIED`
   - 셀 내부 쓰기 차등 2건 (analyst×ticket은 "읽기" 칸이므로): analyst의 `ticket__create_ticket` 거부 + `ticket__update_status` 거부
2. `POLICY_DENIED` payload가 형식 계약과 정확히 일치 (`rule` 값 `support-agent:ops:query_logs` 형식 검증 포함)
3. 인자 레벨: analyst-agent `query_logs` 24h 초과 → 거부 + `detail` 필드, **정확히 24h → 성공** (경계값, `<=` 계약 고정), 24h 이내 → 성공, **dev-agent는 동일 호출 26h도 성공**
4. 인증 3분기(헤더 없음/서명 불일치/만료 토큰): tools/call은 전부 `AUTH_FAILED` + 올바른 `reason`, **무토큰 tools/list는 HTTP 401** (이중 계층 검증)
5. 미등록 agent_id 토큰 → 모든 tool 거부 (default-deny 검증)
6. audit JSONL: 허용 1건·거부 1건·인증실패 1건·오류 1건(`UNKNOWN_TOOL` 호출) 후 각각 1줄씩, 전 필드 존재, `args_summary` ≤ 256자
6b. 인자 정책 이상 입력 3종(파싱 불가 / `end < start` / naive·aware 혼용) → 전부 `POLICY_DENIED` + `detail`, 비크래시
6c. `policy.yaml`에 미존재 tool 이름 삽입 → 로드 시 경고 로그 (단위 테스트)
6d. audit 쓰기 실패(쓰기 불가 경로 주입) → tool 호출은 정상 성공 + 에러 로그 (가용성 > 감사 완결성 검증)
7. `scripts/issue_tokens.py` 출력 토큰 3개가 위 전체 테스트의 픽스처로 동작
8. 기존 S1-S3 테스트 그린 유지 (단, 무인증 호출이 막히므로 S3 테스트는 토큰 픽스처로 갱신)

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | policy.py 평가 로직 (default-deny, 인자 정책, 미등록 agent) | +6 |
| Unit | policy.py 이상 입력 3종 (AC 6b) + 로드 시 검증 (AC 6c) | +4 |
| Unit | auth.py 3분기, audit.py 절단·append | +5 |
| Unit | audit 쓰기 실패 허용 (AC 6d) | +1 |
| Integration | 9칸 매트릭스 (AC 1) | +9 |
| Integration | 인자 정책 4케이스(경계 포함) + 인증 3분기 + tools/list 401 + audit 4종 decision | +10 |

## Rollback Plan

정책은 데이터(YAML)와 코드가 분리되어 있다 — 정책 사고는 `policy.yaml` revert만으로 복구. 코드 문제는 커밋 revert. audit 파일은 append-only라 롤백 불요.

## Effort Estimate

JWT 0.5일 + 정책 엔진(인자 정책 포함) 1.5일 + audit 0.5일 + 9칸 테스트·기존 테스트 갱신 1일 + 버퍼 0.5일 ≈ 4일

## Files Reference

| File | Change |
|---|---|
| `gateway/src/gateway/{auth,policy,audit}.py` | 신규 |
| `gateway/src/gateway/routes.py` | 인증·정책 호출 경로 삽입 |
| `policies/policy.yaml` | 신규 |
| `scripts/issue_tokens.py` | 신규 |
| `tests/integration/test_policy_matrix.py` | 신규 — 9칸 |
| `tests/integration/test_auth_audit.py` | 신규 |
| `tests/integration/test_gateway_core.py` | 토큰 픽스처 적용 |
| `docker-compose.yml` | `GATEWAY_JWT_SECRET`, `GATEWAY_AUDIT_PATH` 주입 |

## Out of Scope

- JWT 발급 서버, 토큰 갱신/폐기
- 정책 핫 리로드, OPA/Rego (S5 stretch 최후순위)
- audit 조회 UI (S6), audit 무결성 서명

## Related

- Epic `00-epic.md` · 선행: S3 · 다음: S5 `05-observability.md` (이 spec의 AC 1 통과가 진입 조건)
- 거부 payload 형식은 S6 `06-langgraph-admin.md` 와의 계약
