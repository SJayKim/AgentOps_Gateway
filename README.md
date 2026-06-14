# AgentOps Gateway

여러 MCP 서버를 단일 진입점(:8000)으로 묶어 라우팅·인증·정책·감사·관측을
담당하는 MCP Gateway. 스펙은 `docs/specs/` 참조.

## 아키텍처

```
                       ┌──────────────────────────┐
 AI Agents ──JWT──▶    │  Gateway (:8000)          │      ┌─ ticket-server (:8101)
 (support/analyst/dev) │  인증 → 정책(default-deny)│──▶   ├─ docs-server   (:8102)
                       │  → 라우팅 → audit·메트릭  │      └─ ops-server    (:8103)
                       └────────────┬─────────────┘
                              /metrics │ scrape 5s
                       Prometheus (:9090) ──▶ Grafana (:3000, 자동 프로비저닝)
```

## 기동

```bash
docker compose up --build --wait   # 6서비스 원커맨드 기동
```

- Gateway MCP 엔드포인트: `http://localhost:8000/mcp` (Streamable HTTP)
- Prometheus: `http://localhost:9090` · Grafana: `http://localhost:3000`
  (대시보드 "AgentOps Gateway" 자동 프로비저닝 — 패널: 정책 거부 카운트,
  클라이언트별 호출량, latency p50/p99)

## 인증 (S4)

- 토큰은 정적 사전발급: `GATEWAY_JWT_SECRET=<secret> uv run python scripts/issue_tokens.py`
- **docker-compose의 `GATEWAY_JWT_SECRET`은 데모값이다. 실제 운영 secret은
  레포에 두지 않는다** — 배포 환경의 secret 관리자에서 주입할 것.
- 정책은 `policies/policy.yaml` (default-deny), 감사 로그는 `audit/audit.jsonl`
  (append-only JSONL, 경로는 `GATEWAY_AUDIT_PATH`). 각 레코드의 `trace_id`는
  OTel trace ID — 게이트웨이 로그와 대조해 요청 단위 추적 가능.

## E2E 데모 (LLM 불필요)

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/e2e_demo.py
```

support-agent로 성공(ticket 생성) → 성공(docs 검색) → 거부(ops 로그 조회,
`POLICY_DENIED`) 시나리오를 완주하면 exit 0. CI의 `e2e` 잡이 compose 기동 후
같은 스크립트를 실행한다 — 데모가 깨지면 CI가 빨갛다.

## audit admin 페이지 (S6)

`docker compose up` 후 브라우저에서 한 번만 토큰으로 진입하면 쿠키가 설정된다:

```
http://localhost:8000/admin?token=demo-admin-token-do-not-use-in-prod
```

- 상단: 지난 24h **agent×server 거부 집계** — "누가 어느 서버에 접근을 거부당했나"
- 본문: audit JSONL 역순 테이블 (ts·agent·tool·decision·args·trace_id, 거부 행 강조)
- 필터: agent / decision / 지난 N시간 (폼 GET)
- 토큰은 `ADMIN_TOKEN` env. **데모 한정 인증** — 실서비스 세션·RBAC는 Out of Scope.

## LangGraph support-agent 데모 (S6, 수동 실행)

Week 1-3의 "외부 의존성 0" 제약에서 벗어나는 **유일한 실 LLM 사용 지점**이다. compose에
넣지 않고 (API 키 필요) 수동 실행한다:

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
ANTHROPIC_API_KEY=<your-key> \
uv run python -m demo_agent
```

support-agent가 ticket·docs 검색(허용) → `ops__query_logs`(거부) 수신 → **거부 payload의
`rule`을 파싱해 우회 계획을 출력**하고 대안(docs 검색)을 실행한다. 거부 분기는 LLM 프롬프트가
아니라 **그래프 노드**(`demo_agent.graph.route_after_tools`)로 보장한다 — LLM이 거부를
generic 오류로 뭉개지 못하게 구조로 차단.

### 수동 검증 체크리스트 (AC1·2·6 — CI 제외)

- [ ] 시나리오 4단계 완주, 3단계 출력에 `POLICY_DENIED`의 `rule` 값 포함 (AC1)
- [ ] 우회 계획이 `rule`을 참조하고 대안(docs 검색)을 실제 수행·보고 (AC2)
- [ ] 실행 후 `/admin`에 해당 거부 시도가 나타남 — end-to-end 연결 (AC6)

거부 분기 라우팅·payload 파싱은 LLM 없이 `tests/unit/test_demo_agent.py`로 검증된다 (AC3).

## 알려진 데모 설계 트레이드오프

- `tools/list`는 정책 필터링 없이 전체 tool을 반환한다 — support-agent가
  ops tool의 존재를 알아야 "호출 시도 → 거부" 시연이 성립하기 때문.
  production이라면 정책 기반 목록 필터링이 기본값이어야 한다.
- audit의 `args_summary`는 256자 절단만 한다 — production이라면 필드별
  redaction(민감 인자 마스킹)이 필요하다.
