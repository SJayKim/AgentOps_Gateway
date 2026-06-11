# EPIC: AgentOps Gateway — 4주 구현

> 근거 설계 문서: `docs/design/agentops-gateway-design.md` (2026-06-10 APPROVED)
> 작성: 2026-06-11 /spec · 브랜치: main

## Context

한 회사의 AI 에이전트 3종(support/analyst/dev)이 각자 다른 권한으로 사내 도구(ticket/docs/ops)에 접근해야 하지만, 에이전트가 MCP 서버에 직접 연결하는 구조에서는 tool call 단위 권한 강제와 감사가 불가능하다. 그 한가운데에 Gateway를 놓아 라우팅·인증·정책·관측을 단일 진입점에서 해결한다. 이 4주 산출물은 시뮬레이션 환경 위의 데모 시스템이다 (설계 문서 전제 1). 레포는 그린필드 — 2026-06-11 기준 추적 파일은 `.claude/` 설정, `CLAUDE.md`, `docs/` 뿐, 코드 0줄.

**프로젝트의 뼈대 — 권한 매트릭스 3×3:**

| 에이전트 | ticket | docs | ops |
|---|---|---|---|
| support-agent | 읽기+쓰기 | 읽기 | ❌ 차단 |
| analyst-agent | 읽기 | 읽기 | 읽기 |
| dev-agent | 읽기+쓰기 | 읽기 | 읽기+쓰기 |

"Gateway가 없으면 이 매트릭스를 강제할 방법이 없다"가 곧 존재 이유다 (전제 3).

## 확정 기술 결정 (전 spec 공통)

| 항목 | 결정 | 출처 |
|---|---|---|
| 언어/버전 | Python 3.12 고정 | /spec D4 |
| 패키징 | uv workspace — 패키지 4개 모노레포 | /spec D4·D5 |
| Gateway 프레임워크 | FastAPI + Streamable HTTP transport | 설계 문서 |
| MCP | MCP Python SDK — S2 의존성 추가 시점의 최신 안정 버전으로 `uv.lock`에 고정, Streamable HTTP 클라이언트·서버 API 지원을 추가 시점에 확인 | 설계 문서 + codex 게이트 지적 |
| 테스트 | pytest, 통합 테스트는 루트 `tests/integration/` | /spec Phase 4 |
| 린트 | ruff (lint + format) | /spec |
| CI | GitHub Actions — lint + 통합 테스트 | 설계 문서 |
| 포트 | gateway 8000 / ticket 8101 / docs 8102 / ops 8103 | /spec |

## Child Issues

| # | Spec 파일 | 범위 | Effort | 의존 |
|---|---|---|---|---|
| S1 | `01-scaffold.md` | uv workspace, CI, compose 뼈대 | ~1일 | — |
| S2 | `02-backend-servers.md` | 백엔드 MCP 서버 3종 | ~2-3일 | S1 |
| S3 | `03-gateway-core.md` | Gateway 코어 (Week 1) | ~2-3일 | S2 |
| S4 | `04-auth-policy-audit.md` | JWT + 정책 엔진 + audit (Week 2) | ~4-5일 | S3 |
| S5 | `05-observability.md` | OTel/Prometheus/Grafana + E2E (Week 3) | ~4-5일 | S4 |
| S6 | `06-langgraph-admin.md` | (선택) LangGraph 데모 + admin (Week 4) | ~4-5일 | S4 필수, S5 권장 |

## Dependency Graph

```
S1 ──> S2 ──> S3 ──> S4 ──> S5 ──> (S6, 선택)
                       └──────────────┘  S6는 S4만 필수 의존
```

## Sequencing Rationale

- Gateway(S3)는 라우팅 대상 백엔드(S2)가 먼저 존재해야 통합 검증이 가능하다.
- 정책(S4)은 S3의 라우팅 경로 위에 미들웨어로 얹히므로 코어가 먼저다.
- **S4의 9칸 매트릭스 통합 테스트가 통과하기 전에 S5에 진입하지 않는다** — 설계 문서 리스크 완화 조건. Week 3가 솔로 프로젝트의 통상 이탈 지점이다.
- S5에서 시간이 모자라면 Grafana 패널 수를 줄이되, 정책 거부 카운트 메트릭은 절대 빼지 않는다.
- S6는 The Assignment(첫 대상 회사와의 대화) 결과에 따라 시나리오가 바뀔 수 있어 마지막이며 선택이다.

## 병행 작업 (코드 아님)

**The Assignment**: 늦어도 S1-S3 진행과 병행하여, 염두에 둔 회사 담당자와 첫 대화를 잡는다. 질문 하나 — "에이전트가 사내 도구에 접근하는 걸 지금 어떻게 통제하고 있어요?" 이 답이 S6 데모 시나리오를 실제 상황으로 바꾸는 유일한 입력이다.

## Definition of Done (설계 문서 Success Criteria 채택)

1. 권한 매트릭스 9칸 전부 통합 테스트로 검증 — 허용 칸 성공, 금지 칸 `POLICY_DENIED` 응답 (S4)
2. 인자 레벨 정책 1개(`query_logs` 시간 범위 ≤24h) 테스트 통과 (S4)
3. 스크립트 클라이언트(LLM 불필요)가 support-agent 토큰으로 ticket 생성 → docs 검색 → ops 호출 시도 → `POLICY_DENIED` 수신까지 E2E 동작 (S5 종료 시점)
4. audit log JSONL에 허용/거부가 trace ID와 함께 기록 (S4 기록, S5 trace ID 연동)
5. Grafana에서 클라이언트별 호출량 / latency p50·p99 / 정책 거부 카운트 확인 가능 (S5)
6. `docker compose up` 한 번으로 전체 데모 환경 기동 (S5)
7. (S6 진행 시) LangGraph support-agent가 거부 응답을 받아 우회 계획을 출력
8. (stretch 달성 시) 백엔드 1개 강제 다운 시 circuit breaker가 해당 백엔드 tool을 aggregation에서 제외

## Out of Scope (전 spec 공통)

- tools/list의 정책 필터링 — 의도적 미포함. 모든 에이전트가 전체 목록을 보고 거부는 호출 시점에 발생 (S6 데모의 전제)
- JWT 발급 서버 — 정적 사전발급 스크립트만
- PyPI 등 패키지 배포 — 실수요 확인 전까지 보류
- 실서비스 멀티테넌시, HA, 수평 확장
- rate limiting / circuit breaker / OPA — S5 stretch 섹션 외 어디서도 다루지 않음
- 거버넌스 wedge(Approach C 요소) — The Assignment 결과에 따라 별도 세션에서 재검토

## Related

- 설계 문서: `docs/design/agentops-gateway-design.md`
- 구현 착수 전 `/plan-eng-review` 권장 (체크포인트 2026-06-10 합의)
