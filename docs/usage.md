# 직접 써보기 — 실행 + 파이프라인 중간 단계 눈으로 보기

이 문서의 모든 명령과 출력은 **2026-09-12에 실제로 실행해 확인한 것**이다(compose 경로,
Windows 11 + Docker Desktop + Git Bash). 스펙은 `docs/specs/`, K8s 실측은
`docs/k8s-stateful-findings.md`를 본다.

- 셸은 **Git Bash 기준**이다. PowerShell이면 `VAR=x cmd` 대신 `$env:VAR="x"; cmd`.
- 전제: Docker Desktop 실행 중, `uv` 설치됨. 그 외 외부 의존성·API 키는 없다
  (§8 LangGraph 데모만 예외).

---

## 1. 60초 기동

```bash
docker compose up --build --wait    # 6서비스 전부 healthy가 될 때까지 대기
```

`--wait`가 핵심이다. `depends_on`은 기동 순서일 뿐 준비 보장이 아니라서, 이게 없으면
아래 e2e가 백엔드보다 먼저 붙어 실패한다.

| 서비스 | 주소 | 무엇 |
|---|---|---|
| gateway | http://localhost:8000/mcp | MCP 단일 진입점 (Streamable HTTP) |
| gateway | http://localhost:8000/admin | audit 관리 페이지 |
| gateway | http://localhost:8000/metrics · /health · /ready | 관측·프로브 |
| ticket-server | :8101 | SQLite 티켓 |
| docs-server | :8102 | BM25 문서 검색 (corpus 10개) |
| ops-server | :8103 | 가짜 메트릭·로그 (결정적 생성) |
| prometheus | http://localhost:9090 | 5초 스크레이프 |
| grafana | http://localhost:3000 | 대시보드 "AgentOps Gateway" (익명 로그인) |

정상 동작 한 방 확인:

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/e2e_demo.py
```

```
[e2e] step 1 OK - ticket__create_ticket succeeded
[e2e] step 2 OK - docs__search_docs succeeded
[e2e] step 3 OK - ops__query_logs denied (code=POLICY_DENIED, rule=support-agent:ops:query_logs)
[e2e] scenario complete: success-success-denied     # EXIT=0
```

**3번에서 거부를 받는 것이 성공이다.** 거부가 안 나면 정책에 구멍이 뚫린 것이라 exit 1.

---

## 2. 토큰 — 누가 되어 호출할 것인가

게이트웨이는 `Authorization: Bearer <JWT>`의 `agent_id` claim만 본다. 발급 서버는 없고
스크립트가 3개를 찍어준다(유효기간 30일).

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/issue_tokens.py
# support-agent: eyJhbGciOiJIUzI1NiIs...
# analyst-agent: ...
# dev-agent: ...
```

> `InsecureKeyLengthWarning`이 같이 뜬다 — 데모 secret이 30바이트라 그렇다. 무시해도 되고
> `2>/dev/null`로 지워도 된다.

권한 매트릭스(`policies/policy.yaml`, **default-deny** — 미기재 조합은 전부 거부):

| agent | ticket | docs | ops |
|---|---|---|---|
| support-agent | create/search/update | search/read | ❌ 전부 거부 |
| analyst-agent | search만 | search/read | get_metrics, query_logs(**최대 24h**) |
| dev-agent | 전부 | 전부 | 전부 |

---

## 3. 아무 tool이나 직접 호출하기

시나리오 스크립트(`e2e_demo.py`)는 정해진 3단계만 돈다. **에이전트·tool·인자를 마음대로
바꿔 보려면** 아래를 `scripts/call_tool.py`로 저장한다(레포에 커밋된 파일은 아니다).

```python
import asyncio, json, os, sys
sys.path.insert(0, "scripts")
from issue_tokens import issue_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    token = issue_token(os.environ["AGENT"], os.environ["GATEWAY_JWT_SECRET"])
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(os.environ["TOOL"], json.loads(os.environ.get("ARGS", "{}")))
            print(f"isError={res.isError}")
            for block in res.content:
                print(block.text)

asyncio.run(main())
```

쓰는 법 — 환경변수 3개(`AGENT` / `TOOL` / `ARGS`)만 바꾼다. 레포 루트에서 실행할 것.

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
AGENT=support-agent TOOL=ticket__create_ticket \
ARGS='{"title":"printer down","body":"3F printer offline"}' \
uv run python scripts/call_tool.py
```
```
isError=False
{"id": 2, "status": "open"}
```

tool 이름은 **`<백엔드>__<tool>`** 형태다(게이트웨이가 prefix를 붙여 집계한다). 전체 7개:
`ticket__create_ticket` `ticket__search_tickets` `ticket__update_status`
`docs__search_docs` `docs__read_doc` `ops__get_metrics` `ops__query_logs`.

### 네 가지 응답을 다 보기 — 이게 게이트웨이의 전부다

거부·오류도 예외가 아니라 **`isError=true`인 MCP result**로 온다. 본문은 항상
`{"code": ...}` JSON 한 봉투(`errors.py`가 단일 생성 지점).

```bash
# ① 허용
AGENT=dev-agent TOOL=ops__get_metrics ARGS='{"metric":"cpu"}'
#    → isError=False, 24시간치 시계열

# ② 미기재 조합 → 정책 거부
AGENT=analyst-agent TOOL=ticket__create_ticket ARGS='{"title":"t","body":"b"}'
#    → {"code": "POLICY_DENIED", "rule": "analyst-agent:ticket:create_ticket", "agent": "analyst-agent"}

# ③ 인자 레벨 제약 위반 → 같은 거부, detail이 붙는다
AGENT=analyst-agent TOOL=ops__query_logs \
ARGS='{"query":"ERROR","start":"2026-01-01T00:00:00Z","end":"2026-01-03T00:00:00Z"}'
#    → {"code": "POLICY_DENIED", ..., "detail": "time range 48h exceeds max 24h"}
#    end를 06:00Z로 줄이면 통과한다 — 정책이 인자까지 본다는 증거

# ④ 없는 tool
AGENT=support-agent TOOL=nosuch__tool ARGS='{}'
#    → {"code": "UNKNOWN_TOOL", "tool": "nosuch__tool"}
```

`rule`이 `"<agent>:<server>:<tool>"` 형식인 건 계약이다 — 에이전트가 이걸 파싱해
"무엇이 막혔는지" 알고 우회 계획을 세운다(§8).

### 보이는 목록 확인

같은 요령으로 `scripts/list_tools.py`:

```python
import asyncio, os, sys
sys.path.insert(0, "scripts")
from issue_tokens import issue_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    token = issue_token(os.environ.get("AGENT", "dev-agent"), os.environ["GATEWAY_JWT_SECRET"])
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print(sorted(t.name for t in (await s.list_tools()).tools))

asyncio.run(main())
```

support-agent로 해도 7개가 다 보인다. **의도된 설계다** — 거부 시연이 성립하려면 에이전트가
ops tool의 존재는 알아야 한다. production이면 정책 기반 목록 필터링이 기본값이어야 한다.

---

## 4. 파이프라인 중간 단계 눈으로 보기

한 번의 `tools/call`이 지나는 길은 하나다: **인증 → 정책 → 라우팅 → 관측 → 감사**
(`app.py`의 `call_tool` 함수 하나. 미들웨어 추상화를 일부러 안 만들었다). 그 각 단계를
아래 다섯 창으로 본다. **창을 4개 띄워 놓고 §3의 호출을 하나씩 던지는 게 제일 잘 보인다.**

### ① OTel span — 단계가 통째로 보인다 (제일 중요)

```bash
docker compose logs -f gateway
```

호출 한 건마다 span이 콘솔에 JSON으로 찍힌다. 이름은 4종:

```
tools/call        ← 최상위 (호출 한 건 전체)
  auth            ← JWT 검증
  policy          ← default-deny 평가
  backend_call    ← 실제 백엔드 중계
```

**거부된 호출에는 `backend_call` span이 아예 없다.** 실측으로 6건을 호출했을 때
`tools/call` 6 / `auth` 6 / `policy` 6 / **`backend_call` 3** — 허용 3건만 백엔드까지 갔다는
뜻이다. 직접 세보려면:

```bash
docker compose logs gateway | grep -oE '"name": "[a-z_/]+"' | sort | uniq -c
```

각 span의 `trace_id`(예: `0xca90030daa2d55d299c063668c233ec6`)를 적어 둔다 — ②③에서 쓴다.

> 참고: `logger.info("tools/call ... decision=...")` 한 줄짜리 앱 로그는 uvicorn 기본
> 로깅 설정에서 활성화되지 않아 안 보인다. 위 span이 그 역할을 대신한다.

### ② audit JSONL — "누가 무엇을 시도했나"의 영구 기록

append-only. 수정·삭제 코드 경로가 아예 없다.

```bash
docker compose exec gateway tail -f /app/audit/audit.jsonl
```

```json
{"ts": "2026-09-12T03:51:54.605620+00:00", "agent": "analyst-agent",
 "tool": "ops__query_logs",
 "args_summary": "{\"query\": \"ERROR\", \"start\": \"2026-01-01T00:00:00Z\", \"end\": \"2026-01-03T00:00:00Z\"}",
 "decision": "denied", "trace_id": "97cf70a32f9638df20d8c43cdfdb9a71"}
```

- `decision` 5종(`allowed` / `denied` / `auth_failed` / `rate_limited` / `error`)은
  **메트릭 라벨과 같은 어휘**다. 대시보드의 "거부 12건"과 audit의 "denied 12줄"이 어긋나면
  둘 중 하나가 버그다.
- `trace_id`가 ①의 span과 **같은 값**이다(`0x` 접두사만 뺀 것).
- `args_summary`는 256자 절단만 한다 — production이면 필드별 redaction이 필요하다.

⚠️ **compose gateway에는 볼륨이 없다.** audit 파일은 컨테이너와 함께 사라진다
(`docker compose down` 하면 끝). 알려진 공백이고 `TODOS.md` § B가 소유한다.

### ③ /admin — 거부를 사람 눈으로

```
http://localhost:8000/admin?token=demo-admin-token-do-not-use-in-prod
```

최초 1회 토큰으로 들어가면 쿠키가 심긴다(이후 `?token=` 없이 접근). 토큰 없이 들어가면 403.

- 상단: 지난 24h **agent×server 거부 집계** — "누가 어느 서버에서 막혔나"
- 본문: audit 역순 테이블 (ts·agent·tool·decision·args·trace_id, 거부 행 강조)
- 필터: `?agent=analyst-agent&decision=denied&since=1` (폼으로도 가능)

### ④ /metrics + Prometheus — 숫자로

```bash
curl -s http://localhost:8000/metrics | grep -E "^gateway_(policy_denied|tool_calls)_total"
```
```
gateway_tool_calls_total{agent="support-agent",decision="allowed",server="ticket",tool="create_ticket"} 1.0
gateway_tool_calls_total{agent="support-agent",decision="denied",server="ops",tool="query_logs"} 1.0
```

메트릭 3종: `gateway_tool_calls_total`(agent·server·tool·decision) /
`gateway_tool_call_duration_seconds`(히스토그램) / `gateway_policy_denied_total`(핵심 메트릭).

Prometheus에 직접 물어보기:

```bash
curl -s --get http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (agent) (gateway_policy_denied_total)'
# {"result":[{"metric":{"agent":"support-agent"},"value":[...,"1"]},
#            {"metric":{"agent":"analyst-agent"},"value":[...,"2"]}]}
```

웹 UI는 http://localhost:9090 → Graph. 스크레이프 주기는 5초라 호출하고 바로 보인다.

### ⑤ Grafana 대시보드

http://localhost:3000 → 대시보드 **"AgentOps Gateway"** (자동 프로비저닝, 익명 Admin).
패널 3개:

1. **정책 거부 카운트 (agent별)** — `sum by (agent) (increase(gateway_policy_denied_total[5m]))`
2. **클라이언트별 호출량** — `sum by (agent) (rate(gateway_tool_calls_total[1m]))`
3. **Latency p50/p99 (tool별)** — `histogram_quantile(...)`

§3의 거부 호출을 몇 번 던지고 대시보드를 보면 1번 패널이 올라간다.

### 한 요청을 끝까지 따라가기

1. §3으로 거부 호출 하나를 던진다.
2. `docker compose logs gateway`에서 방금 `tools/call` span의 `trace_id`를 복사한다.
3. 같은 span 블록에 `auth`·`policy`는 있고 `backend_call`은 없다 → **정책에서 끊겼다.**
4. `docker compose exec gateway grep <trace_id> /app/audit/audit.jsonl` → 같은 건의 audit 줄.
5. `/admin`에서 그 행이 거부로 강조돼 있고, 상단 집계 숫자가 1 올라가 있다.
6. `/metrics`의 `gateway_policy_denied_total{agent=...}`도 1 올라가 있다.

**같은 사실이 네 곳에 같은 값으로 남는지**를 보는 절차다. 어긋나면 그게 버그다.

---

## 5. 백엔드(ops 포함) 기능 직접 만지기

게이트웨이를 거치지 않고 백엔드가 뭘 노출하는지만 보려면:

```bash
uv run python scripts/check_servers.py
# :8101 -> ['create_ticket', 'search_tickets', 'update_status']
# :8102 -> ['search_docs', 'read_doc']
# :8103 -> ['get_metrics', 'query_logs']
```

### ops-server — 결정적 가짜 데이터

외부 의존성 0이 설계 제약이라 데이터를 코드가 만든다. **같은 입력이면 항상 같은 출력**이다
(기준 시각 2026-01-01 고정, 시각 기반 시드).

```bash
AGENT=dev-agent TOOL=ops__get_metrics ARGS='{"metric":"cpu"}'
# metric은 cpu | memory | requests 셋뿐. 24시간치 시간당 1포인트
#   {"metric":"cpu","points":[{"ts":"2026-01-01T00:00:00+00:00","value":40.0}, ...]}

AGENT=dev-agent TOOL=ops__query_logs \
ARGS='{"query":"ERROR","start":"2026-01-01T00:00:00Z","end":"2026-01-01T06:00:00Z"}'
#   {"lines":["2026-01-01T03:00:00+00:00 ERROR [gateway] request completed"],"count":1}
```

`query_logs`의 **시간 범위 제한은 백엔드가 하지 않는다** — 정책(게이트웨이)의 몫이다.
그래서 dev-agent는 48시간도 조회되고 analyst-agent는 §3-③처럼 막힌다. 같은 백엔드,
같은 tool, 다른 결과 — 그게 게이트웨이가 존재하는 이유다.

### docs-server — BM25 검색

corpus 10개(`servers/docs/corpus/`): `api-conventions` `code-review-checklist`
`database-backup` `deployment-process` `incident-runbook` `monitoring-alerts`
`onboarding-guide` `oncall-rotation` `security-policy` `vacation-policy`.

```bash
AGENT=support-agent TOOL=docs__search_docs ARGS='{"query":"backup rotation"}'
# 상위 5개가 doc_id·score·snippet으로. score 순 정렬이라 랭킹이 보인다
AGENT=support-agent TOOL=docs__read_doc ARGS='{"doc_id":"incident-runbook"}'
```

### ticket-server — SQLite

`create_ticket` → `{"id": N, "status": "open"}` → `search_tickets` / `update_status`.
데이터는 컨테이너 안에 있다(볼륨 없음 — 내리면 사라진다).

---

## 6. 실패·복원력 기능 눈으로 보기

두 stretch 기능(rate limit / circuit breaker)은 **env가 없으면 꺼져 있다.** compose 파일에는
안 들어 있으므로, 켜서 보려면 게이트웨이만 로컬로 띄운다(백엔드는 compose 그대로 쓴다).

```bash
docker compose stop gateway          # :8000을 비운다
```

### rate limit — 클라이언트별 토큰 버킷

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
GATEWAY_RATE_LIMIT=3 GATEWAY_RATE_REFILL=0 \
uv run python -m gateway             # 다른 창에서
```

`GATEWAY_RATE_REFILL=0`이 요령이다 — 회복을 끄면 **통과한 횟수가 곧 실효 한도**가 된다.
같은 호출을 5번 반복하면:

```
call 1  isError=False
call 2  isError=False
call 3  isError=False
call 4  isError=True  {"code": "RATE_LIMITED", "agent": "dev-agent"}
call 5  isError=True  {"code": "RATE_LIMITED", "agent": "dev-agent"}
```

audit에도 `decision: "rate_limited"`로, 메트릭에도 같은 라벨로 남는다.
(버킷은 **파드/프로세스 로컬**이다. 레플리카 3이면 실효 한도가 3배가 된다 —
K8s 실측은 findings §3.)

### circuit breaker — 백엔드가 죽었을 때

```bash
docker compose stop ops-server
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
GATEWAY_CIRCUIT_THRESHOLD=2 GATEWAY_CIRCUIT_COOLDOWN=30 \
uv run python -m gateway
```

ops tool을 3번 호출하면 전부 `{"code": "BACKEND_UNAVAILABLE", "server": "ops"}`인데,
`threshold=2`라 **연속 2회 실패로 회로가 열려 3번째부터는 백엔드에 시도조차 하지 않는다**
(fail-fast). audit에는 세 건 다 `decision: "error"`로 남는다. 눈에 확실히 보이는 건 목록이다:

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/list_tools.py
# ['docs__read_doc', 'docs__search_docs', 'ticket__create_ticket',
#  'ticket__search_tickets', 'ticket__update_status']     ← ops 2개가 사라졌다 (7 → 5)
```

30초(cooldown) 뒤 half-open이 되어 딱 한 번 떠보고, ops-server를 되살려 두었으면 복구된다.

### /health vs /ready — 왜 둘인가

```bash
curl -s http://localhost:8000/health   # {"status":"ok"}           ← 무조건 ok
curl -s http://localhost:8000/ready    # 백엔드에 실제로 ping을 왕복시킨다
```

ops-server를 끈 상태의 `/ready`:

```json
{"status":"ready","backends":{"ticket":true,"docs":true,"ops":false}}
```

**하나라도 살아 있으면 200이다**(부분 가용성 > 전체 다운). 전멸하면 503.
`/ready`는 상태 플래그를 읽지 않고 `send_ping()`으로 왕복을 시킨다 — K8s에서 다섯 군데가
깨졌을 때 **`/ready`만 거짓말하지 않은 이유**다(findings §7·§9).

### 원복

```bash
docker compose start ops-server gateway   # 로컬 게이트웨이는 Ctrl+C로 끈 뒤
curl -s http://localhost:8000/ready       # 셋 다 true인지 확인
```

---

## 7. K8s에서 보기 (선택)

3노드 k3d 클러스터가 이미 구성돼 있다면(`docs/context/2026-09-05-context-save.md` §2에
재구축 절차 전문):

```bash
kubectl get nodes                                    # 안 붙으면 §2④ — 이름 해석 문제다
kubectl get deploy,pods
GATEWAY_URL=http://localhost:8080/mcp GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
  uv run --project gateway python scripts/e2e_demo.py          # 인그레스 경유 e2e
```

스케일아웃이 무엇을 깨뜨리는지 한 명령으로 재현:

```bash
GATEWAY_URL=http://localhost:8080/mcp GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
  uv run --project gateway python scripts/verify_scaleout.py
```

rate limit 실효 한도(replicas 1 → 5 / replicas 3 → 15), ticket 조용한 소실, 세션 전략
차이(stateful 0/6 → stateless 6/6) 세 가지를 재고 **끝나면 기준선으로 되돌린다**. 결론은
`docs/k8s-stateful-findings.md`.

---

## 8. LLM 에이전트 데모 (API 키 필요)

여기만 실제 LLM을 쓴다. compose에 넣지 않았다.

```bash
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod \
ANTHROPIC_API_KEY=<your-key> uv run python -m demo_agent
```

support-agent가 ticket·docs(허용) → `ops__query_logs`(거부)를 받고 **거부 payload의 `rule`을
파싱해 우회 계획을 출력**한 뒤 대안(docs 검색)을 실행한다. 거부 분기는 프롬프트가 아니라
**그래프 노드**(`demo_agent.graph.route_after_tools`)로 보장한다 — LLM이 거부를 generic 오류로
뭉개지 못하게 구조로 막았다. 실행 후 `/admin`에 그 거부 시도가 남아 있으면 end-to-end 연결 확인.

---

## 9. 정리

```bash
docker compose down          # 컨테이너 제거 (audit·ticket 데이터도 같이 사라진다)
```

---

## 10. 데모 한정 — 실서비스로 오해하면 안 되는 것

- **secret 3종이 전부 데모값이다** (`GATEWAY_JWT_SECRET`, `ADMIN_TOKEN`, Grafana 익명 Admin).
  운영 secret은 레포에 두지 않고 secret 관리자에서 주입한다.
- **audit 영속성이 없다** — compose gateway에 볼륨이 없다. K8s에서 PVC를 붙이면 완결성은
  얻지만 스케일아웃을 잃는다(실측: findings §2). 3주차 방향은 stdout 수집.
- **`tools/list`가 정책 필터링을 하지 않는다** — 거부 시연을 위한 의도적 선택.
- **`args_summary`는 256자 절단뿐** — 필드별 redaction 없음.
- **`/admin`은 토큰 1개 인증** — 세션·RBAC는 범위 밖.
- **rate limit·circuit breaker는 프로세스 로컬 상태** — 레플리카가 늘면 그만큼 배증한다.
