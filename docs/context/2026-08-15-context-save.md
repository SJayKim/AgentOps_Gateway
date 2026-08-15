---
status: handoff
branch: main
timestamp: 2026-08-15T00:00:00+09:00
files_modified:
  - docs/design/k8s-stateful-scale-out.md (REVISED — /plan-eng-review 반영 + 리뷰 리포트)
  - docs/context/2026-08-15-context-save.md (본 문서)
---

## Working on: K8s 설계 `/plan-eng-review` — 7건 결정 완료, 아웃사이드 보이스만 미완료

### Summary

08-14 세션이 scope gate에서 멈춰 있던 `/plan-eng-review`를 재개해 **끝까지 돌렸다.**
설계 문서만 읽지 않고 **게이트웨이 소스 전체(1,263줄)와 백엔드·인프라 파일을 대조**하는
방식으로 진행했다(D1=B 선택).

결론부터: **설계 문서의 코드 주장 6건은 전부 사실이었다.** 전제는 튼튼하다. 대신 문서가
**놓친 것 3건**과 **잘못 프레이밍한 것 1건**이 나왔고, 그중 하나는 4주 계획의 구조 자체를
바꿨다.

코드는 한 줄도 안 건드렸다. 이번 세션 산출물은 설계 문서 개정과 이 핸드오프뿐이다.

---

### 이번 세션에서 확정된 결정

| # | 질문 | 결정 |
|---|------|------|
| **D1** | 리뷰 깊이 | **문서 + 코드 실측 대조** (문서만 읽는 것 대신) |
| **D5** | 4주 중 몇 주를 확정 | **2주만 확정.** 3~4주차는 2주차 말 + The Assignment JD 결과로 재결정 |
| **1** | 세션 전략 | **1C — stateless와 sessionAffinity 둘 다 재현**, findings를 "두 결정" 구조로 재작성 |
| **2** | `replicas: 3` 대상 | **2B — ticket-server도 스케일**, 결론은 "replicas 1로 고정 + 근거" |
| **3** | audit 결론 | **3C — 정확성/영속성 분리.** compose는 안 건드리고 영속성은 TODO로 |
| **4** | probe 3종 | **4B — 1주차에 `/ready` 신설(능동 probe)** |
| **5** | Secret 부재 | **5A — `build_app()`에서 기동 시 검증** (fail-fast) |
| **6** | 2주차 증거 형태 | **6A — 재실행 가능한 스크립트** (`scripts/verify_scaleout.py` 가칭) |
| **7** | probe 타임아웃 | **7A — `/ready`에만 `asyncio.wait_for(..., 2)`** |
| **8** | 아웃사이드 보이스 | **미완료 — 다른 환경에서 재개** |

**D5가 가장 중요하다.** 자르는 게 아니라 순서를 바꾼 것이다. 3~4주차(OTLP/Tempo/
kube-prometheus-stack/KEDA → ArgoCD 또는 kopf)는 버리지 않았고, 결정 시점만 증거가
도착하는 지점으로 옮겼다. 근거는 문서 자신의 문장이다 — *"4주를 태우기 전에 한 시간으로
검증할 수 있는 것을 검증하지 않는 것이 지금 가장 큰 리스크"*.

---

### 코드 실측으로 확인한 것

#### 설계 문서 주장 6건 — 전부 사실 ✅

| 주장 | 확인 위치 |
|---|---|
| MCP 세션이 stateless 아님 | `gateway/src/gateway/app.py:187` `StreamableHTTPSessionManager(app=server)` — `stateless` 미지정 |
| rate limit 버킷이 프로세스 메모리 | `ratelimit.py:34` `self._buckets: dict[str, tuple[float, float]]` |
| audit이 로컬 파일 append | `audit.py:43-48` `open(path, "a", ...)` |
| circuit breaker가 프로세스 메모리 | `circuit.py:43-45` `_failures` / `_opened_at` / `_probing` |
| 정책 핫 리로드 없음 | `app.py:103` `Policy.load(...)` — `build_app()` 진입 시 1회 |
| 백엔드당 세션 1개 + 소유 task | `upstream.py:45-77` `_connection_task` / `_connect_locked` |

#### 문서가 놓친 것 / 틀리게 프레이밍한 것

**① [EUREKA] 네 문제는 네 개가 아니라 두 개의 결정이다**

```
                  ┌──────────────────────────────────────┐
                  │  결정 A: MCP 세션을 어떻게 고치나?     │
                  └──────────────┬───────────────────────┘
       stateless=True            │            sessionAffinity: ClientIP
       (SDK 내장, 1줄)           │            (인프라, 코드 0줄)
          ┌───────────────────────┴───────────────────────┐
          ▼                                               ▼
   요청이 파드에 흩어짐                          한 클라이언트 = 항상 한 파드
          │                                               │
  ┌───────┴────────────────┐                   ┌──────────┴─────────────┐
  │ rate limit  ✗ 실효 3배 │                   │ rate limit  ✓ 자동 해결 │
  │ circuit br. 파드별 학습│                   │ circuit br. 파드별 학습 │
  │ KEDA(3주차) ✓ 유효     │                   │ KEDA(3주차) ✗ 무의미    │
  └────────────────────────┘                   └────────────────────────┘
```

`ratelimit.py:47`의 `allow(self, agent)`가 **IP가 아니라 agent를 키로 쓰기 때문에**,
sessionAffinity로 한 클라이언트를 한 파드에 고정하면 rate limit이 저절로 맞는다.
문서의 2주차 표는 이 넷을 독립 행으로 놓았으나 실제로는 종속이다.

그리고 sticky affinity를 고르면 3주차 KEDA가 자기모순이 된다 — 기존 클라이언트가 파드에
고정돼 스케일아웃이 트래픽을 못 나눠 받는다.

이건 문서를 약화시키는 게 아니라 강화한다. **"네 개를 고쳤다"보다 "네 개인 줄 알았는데
두 개였다"가 훨씬 강한 findings다.**

**② [Layer 1] MCP 세션 문제는 SDK 내장 파라미터 하나**

`.venv/Lib/site-packages/mcp/server/streamable_http_manager.py:70` — `stateless: bool = False`.
docstring: *"creates a completely fresh transport for each request with no session tracking"*.
이 게이트웨이는 서버발 알림·샘플링을 안 쓰므로 잃는 게 없다. 2주차 표의 "선택지 3개 비교"에서
이건 비교가 아니라 기본값이다.

**③ 다섯 번째 stateful 지점 — 문서에 아예 없음**

`servers/ticket/src/ticket_server/db.py:35`
```python
conn = sqlite3.connect(os.environ.get("TICKET_DB_PATH", "tickets.db"))
```
docstring이 직접 쓴다: *"볼륨 마운트 없음(재시작하면 데이터 사라짐)"*.
ticket-server에 `replicas: 3`을 주면 `create_ticket`은 파드 A에, `search_tickets`는 파드 B에
간다 → **에러 없이 빈 결과.** 게이트웨이의 네 문제는 전부 시끄럽게 깨지는데(핸드셰이크 실패,
즉시 거부, 목록 불일치) **이것만 조용하다.** 면접 서사로는 이쪽이 더 강하다.

결론은 "replicas 1로 고정 + 근거". circuit breaker의 "안 고침"과 **종류가 다르다** —
circuit은 "파드별이 의미적으로 옳다", ticket은 "이 컴포넌트는 애초 스케일 대상이 아니다".
성공기준 #2가 요구하는 "고치지 않는다" 결론이 서로 다른 두 종류로 생긴다.

**④ audit 전제가 절반 틀렸다**

`docker-compose.yml`의 gateway 서비스에 **`volumes:` 키가 아예 없다** (10-32행). `.gitignore`에도
`audit/`가 있다. 즉 audit JSONL은 compose에서도 이미 컨테이너와 함께 사라진다.

문서의 *"Evidence Box의 해시 체인은 원리적으로 성립 불가"*는 K8s 탓이 아니다. 정확히는:

- **정확성** — 레플리카 3개 = 독립 체인 3개, 전체 순서 없음 → **K8s가 깨뜨렸다**
- **영속성** — 재시작하면 사라짐 → **원래 없었다**

이 구분을 안 하고 "K8s가 깨뜨렸다"고 쓰면, 데모를 띄워본 사람이 30초에 반증한다.

**⑤ [Layer 1] k3d RWX에 탈출구가 있다**

local-path는 RWO 전용이 맞지만 provisioner에 `sharedFileSystemPath` 옵션이 있어, 모든 노드에
같은 경로가 마운트돼 있으면 RWX를 지원한다. k3d는 노드가 전부 같은 Docker 호스트의 컨테이너라
이 조건을 만들 수 있다. **2주차에 며칠 태우기 전에 30분 검증할 항목.**

주의: 통과해도 그건 "k3d가 단일 호스트라 거짓 통과를 준 것"일 수 있다. 실제 멀티노드 + NFS에선
다르다. 이 함정 자체가 기록 가치가 있다.

---

### 리뷰가 찾은 코드 결함 (승인된 수정)

**[P1] `gateway/src/gateway/auth.py:38` — Secret 부재가 기동이 아니라 요청마다 터진다**
```python
claims = jwt.decode(token, os.environ["GATEWAY_JWT_SECRET"], algorithms=["HS256"])
```
잡는 예외는 `ExpiredSignatureError`/`InvalidTokenError` 둘뿐. `KeyError`는 그대로 올라가고
`app.py:223`의 `except auth.AuthError`도 못 잡는다. → **Secret 없는 파드가 `/health` ok로
Ready가 되고 모든 요청이 500.** compose에선 env가 파일에 있어 절대 안 터지지만, K8s에서
`secretKeyRef` 오타 하나면 정확히 이게 난다.

`app.py:96`이 컨벤션을 직접 명시한다: *"환경 의존은 전부 이 함수 진입 시점에 os.environ에서
한 번 읽는다."* `auth.py`만 이 규칙을 어기고 있다. → **5A: `build_app()`에서 검증.**

`tests/conftest.py:20`이 `setdefault`로 secret을 항상 심으므로 **97 테스트는 안 깨진다.**

**[P1] `upstream.py:96-100` — `/ready`가 행 걸린 백엔드에서 락을 최대 5분 잡는다**
```python
async with self._lock:
    if self._session is None:
        await self._teardown_locked()
        await self._connect_locked()     # → upstream.py:73  await ready  (타임아웃 없음)
```
SDK 기본값이 `timeout=30, sse_read_timeout=300`이고 `httpx.Timeout(30, read=300)`으로 조립된다
(`.venv/.../mcp/client/streamable_http.py:689-711`). **read 타임아웃이 5분.**

**죽은** 백엔드는 TCP RST로 즉시 실패한다. 문제는 **행 걸린** 백엔드다 — TCP는 받는데 핸드셰이크
응답이 없으면 300초까지 락을 쥐고, 그 백엔드로 가는 실제 `tools/call`도 전부 같이 멈춘다.
기존에도 있던 성질이지만 `/ready`는 이걸 probe 주기마다 밟는다. → **7A: `/ready`에만
`asyncio.wait_for(..., timeout=2)`.**

취소 안전성은 확인됨 — `upstream.py:74-76`이 `except BaseException: stop.set(); raise`로
취소를 이미 제대로 처리한다.

**[P2] `app.py:250-252` — `/health`가 무조건 ok**
```python
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
```
`app.py:199-206` lifespan은 백엔드가 전부 죽어도 앱을 띄운다. 세 probe를 전부 여기 걸면
**종류만 셋이고 실제로는 같은 probe 하나**다. → **4B: `/ready` 신설.**

**구현 시 함정 (confidence 8/10):** `/ready`를 단순히 `backend.tools is not None` 체크로 만들면
**교착한다.** 재연결을 트리거하는 건 `aggregate.py:69-73`과 `routes.py:71-76`의 지연 재집계인데
둘 다 요청이 와야 돈다. NotReady 파드는 요청을 못 받는다 → 백엔드가 복구돼도 영원히 NotReady.
**반드시 `ensure_session()`을 직접 호출하는 능동 probe여야 한다.**

**[P2] `app.py:187` — `stateless` 토글은 env여야 한다**
`build_app()`은 100-111행에서 모든 설정을 env로 읽는다. 1C(두 경로 비교)를 이미지 하나로 하려면
`GATEWAY_MCP_STATELESS` env가 필요하다. 하드코딩하면 실험 자체가 불가능.

**[P2] `docs/architecture.md:201`, `:278-309` — 4B가 들어가면 stale**
201행이 `app.py` 책임을 "`/metrics`·`/health` 노출"로 적어 `/ready`가 빠지고, 8장 폴더 지도에
`k8s/`가 없다.

---

### 테스트 커버리지 (승인된 결정이 만드는 경로)

```
CODE PATHS                                          USER FLOWS
[+] gateway/src/gateway/app.py                      [+] 1주차 배포
  ├── build_app() secret 검증 (5A)                    ├── [GAP] [→E2E] e2e_demo Ingress 경유 (기본)
  │   ├── [★  TESTED] secret 있음 — conftest.py:20     ├── [GAP] [→E2E] e2e_demo Ingress 경유 (stateless=on)
  │   └── [GAP]        secret 없음 → 즉시 실패          └── [GAP]        Secret 오타 → CrashLoopBackOff
  ├── /ready (4B, 신규)
  │   ├── [GAP]  백엔드 ≥1 → 200                     [+] 2주차 증거 수집
  │   ├── [GAP]  백엔드 0 → 재연결 성공 → 200            ├── [GAP] [→E2E] rate limit 실효 한도 = N 증명
  │   ├── [GAP]  백엔드 0 → 재연결 실패 → 503            └── [GAP] [→E2E] ticket replicas:3 조용한 소실
  │   └── [GAP]  전멸 → 복구 → Ready 전환 ← 교착 회귀
  ├── /health (기존)
  │   └── [GAP]  테스트 0건 — probe가 1주차 산출물인데 검증 없음
  └── stateless 토글 (1C)
      ├── [GAP]  GATEWAY_MCP_STATELESS=1 경로
      └── [★★★ TESTED] 미설정(기본) — 기존 97개가 이 경로

COVERAGE: 2/14 (14%)  |  Code: 2/9 (22%)  |  Flows: 0/5 (0%)  |  GAPS: 12 (5 E2E)
```

**최우선 갭 2건**
1. **`/ready` 복구 전환 회귀 테스트** — 교착 함정을 막는 유일한 방어선
2. **`stateless=True`에서 e2e 통과** — 1C의 A 경로 전체가 여기 걸려 있다. SDK 1.27 stateless
   모드에서 `streamablehttp_client` 핸드셰이크가 도는지 **검증 전엔 미확정.** 안 돌면 1C 절반이 사라진다.

`build_app()` secret 부재 테스트는 `tests/conftest.py:20`이 import 시점에 `setdefault`하므로
`monkeypatch.delenv` 필요.

---

### Implementation Tasks

- [ ] **T1 (P1, human: ~1일 / CC: ~1시간)** — k8s/ — 1주차 매니페스트 + k3d(agents ≥2) + Ingress 경유 e2e
  - Surfaced by: Step 0 — 이 레포는 이미 12-factor라 리프트는 거의 전부 매니페스트
  - Files: `k8s/base/` (Deployment×4, Service×4, Ingress, ConfigMap, Secret, kustomization)
  - Verify: `GATEWAY_URL=http://<ingress>/mcp uv run python scripts/e2e_demo.py` → exit 0
- [ ] **T2 (P1, human: ~1시간 / CC: ~10분)** — gateway — `build_app()`에서 `GATEWAY_JWT_SECRET` 기동 검증
  - Surfaced by: Code Quality — `auth.py:38` KeyError가 요청마다 발생, `app.py:96` 컨벤션 위반
  - Files: `gateway/src/gateway/app.py`
  - Verify: `monkeypatch.delenv` 후 `build_app()`이 명확한 메시지로 실패
- [ ] **T3 (P1, human: ~3시간 / CC: ~20분)** — gateway — `/ready` 능동 probe + 2초 타임아웃
  - Surfaced by: Architecture 4B + Performance 7A
  - Files: `gateway/src/gateway/app.py`
  - Verify: 백엔드 전멸 → 503, 복구 → 200 전환 테스트
- [ ] **T4 (P2, human: ~2시간 / CC: ~15분)** — gateway — `GATEWAY_MCP_STATELESS` env 토글
  - Surfaced by: 결정 1C — 이미지 하나로 두 경로를 비교하려면 필수
  - Files: `gateway/src/gateway/app.py:187`
  - Verify: 토글 on/off 각각에서 e2e 통과
- [ ] **T5 (P1, human: ~1일 / CC: ~1시간)** — k8s/ — 두 세션 전략 재현 + findings 초안
  - Surfaced by: 결정 1C
  - Files: `k8s/overlays/`, `docs/k8s-stateful-findings.md`
  - Verify: 두 경로의 증상 차이가 문서에 기록됨
- [ ] **T6 (P1, human: ~2시간 / CC: ~15분)** — servers/ticket — replicas:3 조용한 소실 재현
  - Surfaced by: Architecture 2B — `db.py:35` 파드 로컬 SQLite
  - Files: `k8s/base/ticket-server.yaml`, `docs/k8s-stateful-findings.md`
  - Verify: 티켓 생성 후 검색 3회에서 결과가 갈림
- [ ] **T7 (P1, human: ~4시간 / CC: ~30분)** — scripts — `verify_scaleout.py` 증거 수집 스크립트
  - Surfaced by: 결정 6A — 성공기준 #3의 "부하 테스트로 증명"
  - Files: `scripts/verify_scaleout.py` (`spike_concurrency.py` 패턴)
  - Verify: 한 줄 실행으로 rate limit 실효 한도 + ticket 소실 + 세션 전략 차이 출력
- [ ] **T8 (P2, human: ~30분 / CC: ~5분)** — docs — `architecture.md` 갱신
  - Surfaced by: Code Quality — 201행 `/ready` 누락, 8장 폴더 지도에 `k8s/` 없음
  - Files: `docs/architecture.md`
  - Verify: `/ready`와 `k8s/`가 반영됨
- [ ] **T9 (P1, human: ~1일 / CC: ~1시간)** — docs — findings 문서를 "두 결정" 구조로 완성
  - Surfaced by: 결정 1C + 2B + 3C
  - Files: `docs/k8s-stateful-findings.md`
  - Verify: 성공기준 #2 — "고치지 않는다" 결론 2종(circuit breaker, ticket-server) 포함
- [ ] **T10 (P2, human: ~2시간 / CC: ~20분)** — tests — 커버리지 갭 12건 중 코드 경로 7건
  - Surfaced by: Test Review
  - Files: `tests/unit/test_gateway_routing.py` 등
  - Verify: `uv run pytest` 그린 유지

---

### 제안된 TODO (아직 TODOS.md에 안 넣음 — 승인 대기)

1. **`ratelimit.py:42` ConfigMap 값 검증 없음** — `capacity = int(cap)`. "한도를 레플리카 수로
   나눈다"는 10/3을 계산하게 만드는데 `int("3.33")`은 ValueError로 기동 실패. ConfigMap 세계에선
   오타 = CrashLoopBackOff.
2. **`admin.py:49` audit 전체 파일 매 요청 메모리 로드** — `p.read_text()`. docstring의
   "데모 규모에선 충분히 빠르다" 전제는 audit이 컨테이너와 함께 사라져서 성립했다. 3C에서 PVC를
   붙이면 파일이 무한히 자라고 로테이션이 없다.
3. **compose audit 영속성 공백 (Evidence Box 축 선행 과제)** — `docker-compose.yml`에 gateway
   볼륨 없음. 해시 체인의 대전제(단일 프로세스 + 영속 파일)가 지금은 성립 안 함. K8s 축이 아니라
   Evidence Box 축이 소유해야 할 항목.

---

### 미완료 — 다른 환경에서 재개할 것

1. **[재개 지점] 아웃사이드 보이스** — Codex를 `-s read-only`, `model_reasoning_effort=high`,
   `--enable web_search_cached`로 돌렸으나 **5분 타임아웃.** 프롬프트는
   `scratchpad/codex_prompt.txt`에 있었으나 세션 임시 경로라 남지 않는다. 재개 시:
   - `--enable web_search_cached`를 빼고 10분 타임아웃으로 재시도 (권장)
   - 또는 Claude 서브에이전트 폴백 (이 세션은 "요청 없이 Agent 호출 금지" 설정이라 미실행)
   - 물어볼 것: 이 리뷰가 놓친 것, 특히 **"수요 증거 0인데 2주를 쓰는 게 맞나"** 전략 질문
2. **TODOS.md 반영** — 위 제안 3건, 각각 승인 필요
3. **설계 문서 4주차 배치** — D5로 보류. 2주차 말 + JD 5건 수집 후 재결정

### 그대로 남은 기존 과제

- **The Assignment — JD 5건 수집.** 이 트랙의 수요 증거는 여전히 **0**. D5가 3~4주차를 여기에
  묶어놨다. 한 시간이면 2주짜리 결정을 검증한다.
- specs → GitHub 이슈: `scripts/specs_to_issues.sh` 준비됨, `gh` 인증 대기 (변동 없음)

### 상태

- 테스트: 97개 그린 (코드 미변경이므로 유지)
- 코드 변경: **없음** — 이번 세션은 리뷰와 문서만
- 미추적 파일 2건 (이번 커밋에 미포함, 판단 대기): `AGENTS.md`, `.codex/`
  — 7-15경 생성된 Codex CLI 설정. 다른 환경에서 Codex를 쓸 거면 함께 커밋하는 게 낫다.
- gstack: v1.58.5.0 → **v1.64.0.0** 업그레이드됨 (이번 세션 중)
