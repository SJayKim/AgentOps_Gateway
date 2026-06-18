# 학습 자료: AgentOps Gateway 완전 해부

이 폴더는 **코드를 모르는 사람도** AgentOps Gateway의 각 기능을 "무엇을 / 왜 /
어떻게" 순서로 따라갈 수 있게 만든 학습 문서 모음입니다. 기능 1개당 문서 1개로,
각 문서는 담당 소스 파일의 **실제 코드 줄을 인용**해 설계 결정(스펙 S2~S6,
`docs/design/agentops-gateway-design.md`)까지 추적합니다.

> 프로젝트 한 줄 요약: 사내 AI 에이전트들이 사내 도구(티켓·문서·운영 데이터)에
> 접근할 때 거치는 **단일 진입점 MCP Gateway**. 권한 매트릭스(3 에이전트 × 3 서버)를
> tool call 단위로 강제하고, 모든 호출을 감사·관측한다.

## 권장 읽기 순서 (실행 흐름 순)

요청이 시스템을 통과하는 실제 순서대로 읽으면 가장 자연스럽습니다:
**백엔드 → Gateway 코어 → 장애·과부하 방어 → 인증 → 정책 → 감사 → 관측 → admin → demo 에이전트.**

| # | 문서 | 한 줄 설명 | 스펙 |
|---|------|-----------|------|
| 1 | [backend-servers.md](backend-servers.md) | 라우팅 대상이자 권한 매트릭스의 "열"이 되는 백엔드 MCP 서버 3종(ticket·docs·ops) — 직접 구현한 시뮬레이션 환경 | S2 |
| 2 | [gateway-core.md](gateway-core.md) | 모든 에이전트가 연결하는 단일 진입점 — tools/list 집계(prefix 네임스페이싱), 라우팅, 백엔드 세션 유지 | S3 |
| 3 | [resilience.md](resilience.md) | 장애·과부하 방어 — 쏟아지는 호출을 막는 rate limit(토큰 버킷)과 죽은 백엔드를 끊는 circuit breaker. routes.py에 끼운 opt-in 방어막 | S5 stretch / P5 |
| 4 | [auth.md](auth.md) | JWT 인증 — 정적 사전발급 토큰과 검문소. "누가 요청했는가"를 확정하는 첫 관문 | S4 |
| 5 | [policy.md](policy.md) | YAML 정책 엔진 — default-deny로 권한 매트릭스를 tool call 단위로 강제. 프로젝트의 존재 이유 | S4 |
| 6 | [audit.md](audit.md) | append-only JSONL 감사 로그 — 허용/거부/인증실패/레이트리밋/오류 모든 호출의 기록. 거버넌스 증거 계층 | S4 |
| 7 | [observability.md](observability.md) | OTel trace ID + Prometheus 메트릭 + Grafana 대시보드 — "거부가 몇 번 일어났나"를 계기판으로 | S5 |
| 8 | [admin.md](admin.md) | `/admin` 감사 열람 페이지 — "지난 24시간, 누가 민감 tool에 접근 시도했나"에 답하는 화면 | S6 |
| 9 | [demo-agent.md](demo-agent.md) | LangGraph support-agent — 실제 LLM이 정책 거부를 받고 **우회 계획**을 세우는 데모 (유일한 실 LLM 사용 지점) | S6 |

## 각 문서의 공통 골격

모든 문서는 같은 구조를 따릅니다:

- **0. 큰 그림** — ASCII 아키텍처 다이어그램 + 메타포 내러티브 + 파일 매핑 테이블
- **파일별 줄별 해설** — 실제 코드 블록 + "의미(무엇)" / "왜?(대안 대비 이유 + 스펙 근거)"
- **Live wiring** — 이 모듈이 다른 모듈/외부에서 실제로 어떻게 호출되는지
- **관통하는 설계 원칙 요약** — 5~7개 볼드 불릿

## 함께 보면 좋은 문서

- 트리 구조의 이유: [repo-structure.md](repo-structure.md) — "왜 폴더를 이렇게 나눴나"(워크스페이스·의존성 격리·src-layout). 기능 문서를 읽기 전 큰 틀로 먼저 보면 좋다.
- 설계 전체: [`docs/design/agentops-gateway-design.md`](../design/agentops-gateway-design.md)
- 기능별 스펙: [`docs/specs/`](../specs/) (S2~S6)
