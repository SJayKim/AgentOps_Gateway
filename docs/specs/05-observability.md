# S5: Observability — OTel · Prometheus · Grafana + E2E (Week 3)

> Epic: `00-epic.md` · 의존: S4 (9칸 매트릭스 테스트 통과가 진입 조건) · Effort: ~4-5일

## Context

설계 문서가 Week 3를 솔로 프로젝트의 통상 이탈 지점으로 명시했다. 따라서 이 spec은 우선순위가 내장되어 있다: **정책 거부 카운트 메트릭 > 대시보드 > stretch 3종**. 시간이 모자라면 Grafana 패널 수를 줄이되, 거부 카운트 메트릭은 절대 빼지 않는다. E2E 스크립트 클라이언트(LLM 불필요)도 이 spec의 산출물 — Epic DoD 3번의 기준 시점이 Week 3 종료다.

## Current State

S4 완료 시점 기준 Gateway가 인증·정책·audit까지 동작하나 trace ID는 임시 uuid4, 메트릭·대시보드 없음, compose는 4서비스(gateway + 백엔드 3종)만.

## Proposed Change

### P1 (필수 — 절대 미삭제): 거부 카운트 메트릭 + trace ID

- **trace ID**: 모든 요청에 trace ID를 부여하고 백엔드 호출·audit JSONL까지 전파. OpenTelemetry SDK의 trace context를 사용하고, audit 레코드의 `trace_id` 필드를 OTel trace ID로 교체 (S4의 임시 uuid4 대체).
- **메트릭** (Prometheus 형식, Gateway `/metrics` 엔드포인트, `prometheus-client` 사용):

```
gateway_tool_calls_total{agent, server, tool, decision}   # Counter — decision ∈ {allowed, denied, auth_failed, error}
gateway_tool_call_duration_seconds{server, tool}          # Histogram — p50/p99 도출용
gateway_policy_denied_total{agent, server, tool}          # Counter — 핵심 메트릭, 절대 미삭제
```

### P2: Prometheus + Grafana (Docker)

- compose에 `prometheus`(:9090) + `grafana`(:3000) 추가. Prometheus가 gateway `/metrics` 스크레이프 (interval 5s).
- Grafana는 **provisioning으로 코드화** (`observability/grafana/provisioning/` — datasource + dashboard JSON). 수동 클릭 설정 금지 — `docker compose up` 만으로 대시보드가 떠야 함.
- 대시보드 패널 3개 (우선순위 순): ① 정책 거부 카운트 (agent별) ② 클라이언트별 호출량 ③ latency p50/p99 (tool별). 시간이 모자라면 ③→② 순으로 줄이고 ①은 유지.

### P3: OTel trace 계측

- Gateway 요청 처리 경로에 span: 요청 수신 → 인증 → 정책 평가 → 백엔드 호출. exporter는 콘솔(기본) — Jaeger/Tempo 추가는 이 spec 범위 밖 (span 구조만 잡아두면 exporter는 설정 교체).

### P4: E2E 스크립트 클라이언트 (Epic DoD 3)

- `scripts/e2e_demo.py`: LLM 불필요. support-agent 토큰으로 ① `ticket__create_ticket` 성공 → ② `docs__search_docs` 성공 → ③ `ops__query_logs` 호출 → `POLICY_DENIED` 수신·파싱 → 각 단계 결과를 stdout에 사람이 읽을 형태로 출력, 거부 수신 시 exit 0 (시나리오 성공).
- CI에서 compose 기동 후 이 스크립트를 실행하는 잡 추가 — "데모가 깨지면 CI가 빨갛다".
- **readiness (eng review T4)**: `depends_on`은 기동 순서일 뿐 준비 보장이 아니다. 서비스 4종(gateway + 백엔드 3종)에 compose `healthcheck`를 정의하고(경량 HTTP 폴링), CI는 `docker compose up --wait` 로 healthy 대기 후 E2E를 실행한다. E2E 실패 시 `docker compose logs` 를 캡처해 CI 아티팩트로 남긴다 — 플래키한 빨강은 없는 것보다 나쁜 신호다.

### P4b: README 기본판 (eng review T5 — S6에서 이동)

- 아키텍처 개요(다이어그램 1개) + `docker compose up` 기동법 + 토큰 발급(`scripts/issue_tokens.py`) + E2E 실행법 + production 대안 노트 2줄: ① tools/list는 데모를 위해 비필터 (production이면 정책 필터링이 기본값) ② audit `args_summary`는 절단만 하며 production에서는 필드별 redaction 필요.
- S6는 데모 GIF·LangGraph 실행법·수동 체크리스트 **섹션 추가만** 한다. S5 종료 = 공개 가능한 최소 완성품.

### Stretch (P5 — 위 전부 완료 후에만, 이 순서로)

1. **rate limiting**: 클라이언트별 token bucket. 초과 시 거부 payload `{"code": "RATE_LIMITED", ...}` (S4 형식과 동일 패턴)
2. **circuit breaker**: 연속 실패 N회 시 open → 30초 후 half-open 1회 probe → 성공 시 close. open 동안 해당 백엔드 tool은 tools/list에서 제외
3. **OPA/Rego 교체 검토**: 구현이 아니라 스파이크 — YAML 엔진 대비 득실 1페이지 메모

Stretch는 Acceptance Criteria에 포함하지 않는다. 착수 시 별도 커밋으로 분리.

### Implementation Details

- 의존성 추가: `opentelemetry-sdk`, `opentelemetry-api`, `prometheus-client`.
- 메트릭 기록 지점은 audit 기록 지점과 동일한 곳 (decision이 확정되는 단일 지점) — 두 시스템이 같은 사실을 보게.
- compose 최종형: 6서비스 (gateway, ticket, docs, ops, prometheus, grafana). **`docker compose up` 원커맨드 기동이 Epic DoD 6.**

## Acceptance Criteria

1. 거부 호출 1회 후 `/metrics` 에서 `gateway_policy_denied_total{agent="support-agent",server="ops",...}` 이 1 증가
2. audit JSONL의 `trace_id` 가 OTel trace ID 형식(32 hex)이고, 같은 요청의 Gateway 로그 trace ID와 일치
3. `docker compose up` 후 Grafana(:3000)에 대시보드가 자동 프로비저닝되어 패널 3개(거부 카운트/호출량/latency p50·p99)가 데이터를 표시
4. Prometheus(:9090)에서 위 메트릭 3종 쿼리 가능
5. `scripts/e2e_demo.py` 가 성공-성공-거부 시나리오를 완주하고 exit 0, 거부 payload의 `code`/`rule` 을 출력
6. CI에 e2e 잡이 추가되어 그린
7. 기존 전체 테스트 그린 유지

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | 메트릭 레이블 정확성 (decision 4종) | +4 |
| Integration | AC 1, 2 (메트릭 증가·trace 전파) | +2 |
| E2E | `e2e_demo.py` (CI 잡) | +1 |

## Rollback Plan

관측 코드는 요청 경로에 부수 효과만 추가 — 메트릭/trace 코드 revert 시 S4 기능은 무손상. compose의 prometheus/grafana 서비스는 제거해도 나머지 4서비스 독립 동작.

## Effort Estimate

trace ID + 메트릭 1일 + Prometheus/Grafana provisioning 1일 + OTel span 0.5일 + E2E 스크립트·CI 1일 + 버퍼 0.5일 ≈ 4일 (stretch 제외)

## Files Reference

| File | Change |
|---|---|
| `gateway/src/gateway/observability.py` | 신규 — 메트릭·trace 설정 |
| `gateway/src/gateway/{routes,audit}.py` | trace ID 전파, 메트릭 기록 지점 |
| `observability/prometheus.yml` | 신규 |
| `observability/grafana/provisioning/**` | 신규 — datasource + dashboard JSON |
| `scripts/e2e_demo.py` | 신규 |
| `README.md` | 신규 — 기본판 (P4b) |
| `docker-compose.yml` | prometheus, grafana 서비스 추가 + 서비스 4종 healthcheck |
| `.github/workflows/ci.yml` | e2e 잡 추가 |

## Out of Scope

- Jaeger/Tempo 등 trace 백엔드 (exporter 교체만으로 가능한 구조면 충분)
- 알림(Alerting), 장기 메트릭 보존
- rate limit/circuit breaker/OPA — stretch 섹션의 조건(P1-P4 완료) 충족 전 착수 금지

## Related

- Epic `00-epic.md` · 선행: S4 (9칸 테스트 통과 필수) · 다음: S6 `06-langgraph-admin.md` (선택)
