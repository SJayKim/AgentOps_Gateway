# TODOS

> **트랙 상태 (2026-08-18 정리)**
> - **B / 커리어 축 (K8s stateful scale-out) — ACTIVE.** 혼자 끝낼 수 있는 유일한 트랙. 설계 `docs/design/k8s-stateful-scale-out.md` (2026-08-15 REVISED, 2주 확정 / 2026-08-21 D6·D7로 T1 미결정 2건 해소).
> - **A / wedge 축 (Evidence Box) — PARKED.** 진행이 상대 회사 2차 대화 일정에 묶여 있어 자력 진행 불가. 설계 `docs/design/evidence-box-pilot.md` (2026-07-06 APPROVED, 구현 0줄).
> - 두 축은 병존한다. B가 A를 supersede하지 않으며, **compose 경로는 건드리지 않는다** (K8s 설계 § 의도적으로 안 하는 것).

---

## A. 활성 트랙 — K8s stateful scale-out (2주)

원본: `docs/context/2026-08-15-context-save.md` § Implementation Tasks. 착수 순서는 T2 → T3 → T4 → T1 → T5/T6 → T7 → T9 → T8/T10.

### 1주차 — 올리고, 깨뜨린다

- [x] **T2 (P1, CC ~10분)** — `build_app()`에서 `GATEWAY_JWT_SECRET` 기동 검증 ✅ 2026-08-21
  - **Why:** `auth.py:38`이 `os.environ[...]`을 직접 읽어 `KeyError`가 요청마다 터진다. `app.py:223`의 `except auth.AuthError`가 못 잡고, `app.py:96` 컨벤션("환경 의존은 전부 `build_app()` 진입 시 1회")을 이 파일만 어긴다. K8s에서 `secretKeyRef` 오타 하나면 Secret 없는 파드가 `/health` ok로 Ready가 되고 전 요청 500.
  - Files: `gateway/src/gateway/app.py`
  - Verify: `monkeypatch.delenv` 후 `build_app()`이 명확한 메시지로 실패 (`conftest.py:20`이 `setdefault`로 심으므로 delenv 필수)
  - **결과:** `build_app()` 최상단 `RuntimeError` 가드 + `tests/unit/test_app_startup.py`(미설정·빈 문자열 2케이스). 가드 제거 시 `build_app()`이 **성공**하는 것을 확인 — 그게 원래 버그. 빈 문자열을 함께 막은 이유: HS256은 빈 키로도 검증을 수행해 KeyError 없이 전 토큰이 조용히 invalid가 된다. 전체 99 passed.
- [x] **T3 (P1, CC ~20분)** — `/ready` 능동 probe + 2초 타임아웃 ✅ 2026-08-21
  - **Why:** `/health`가 무조건 ok라 probe 3종이 실제로는 같은 probe 하나(4B). `upstream.py:96-100`은 행 걸린 백엔드에서 SDK 기본 `read=300`으로 락을 5분 잡는다(7A).
  - **함정:** `backend.tools is not None` 체크로 만들면 교착한다 — 재연결 트리거(`aggregate.py:69-73`, `routes.py:71-76`)가 요청을 받아야 도는데 NotReady 파드는 요청을 못 받는다. **반드시 `ensure_session()`을 직접 호출하는 능동 probe.**
  - Files: `gateway/src/gateway/app.py`
  - Verify: 백엔드 전멸 → 503, 복구 → 200 전환 테스트 (최우선 회귀 테스트)
  - **결과:** `/ready` 추가 — 백엔드 3종을 동시에 probe하고 하나라도 붙으면 200, 0개면 503. `tests/integration/test_ready.py` 3케이스(전멸→복구, 1개만 죽음, `/health` 불변). 전체 102 passed.
  - **결정 — '전부'가 아니라 '하나라도'.** 설계 4B가 임계값을 안 정했다. 전부를 요구하면 백엔드 1개 사망에 게이트웨이 파드가 LB에서 빠져 나머지 2개로 가는 멀쩡한 요청까지 죽는다. `app.py`의 lifespan과 `aggregate.py`가 이미 "부분 가용성 > 전체 다운"(eng review T1)이라 그 결정을 뒤집지 않았다. 테스트로 고정.
  - **4B 보강 — `ensure_session()`만으로는 죽음을 못 잡는다.** 아래 findings 일곱 번째 참조. `send_ping()` 왕복을 얹어야 통과했다.
  - **부수 결정:** 7A의 `wait_for(..., timeout=2)`를 백엔드마다 순차로 걸면 최대 6초라 kubelet `timeoutSeconds`를 넘긴다. `asyncio.gather`로 동시 실행해 엔드포인트 전체를 2초로 묶었다.
- [x] **T4 (P2, CC ~15분)** — `GATEWAY_MCP_STATELESS` env 토글 ✅ 2026-08-21
  - **Why:** 결정 1C(두 세션 전략 비교)를 이미지 하나로 하려면 필수. 하드코딩하면 실험 자체가 불가능. SDK 1.27 `StreamableHTTPSessionManager(stateless=...)` 기본 False.
  - **선행 리스크:** stateless 모드에서 `streamablehttp_client` 핸드셰이크가 도는지 **미검증.** 안 돌면 1C의 A 경로가 통째로 사라진다 → T5보다 먼저 확인할 것.
  - Files: `gateway/src/gateway/app.py:187`
  - Verify: 토글 on/off 각각에서 e2e 통과
  - **결과 — 선행 리스크 해소. stateless에서 핸드셰이크가 돈다.** `initialize` → `tools/list`(백엔드 3종 집계) → `tools/call`(인증·정책·라우팅)이 세션 id 없이 끝까지 통과했다. **1C의 A 경로는 살아 있다** — T5는 두 전략을 실제로 비교할 수 있다. `app.py` 3줄(env 읽기 + `stateless=` 전달) + `tests/integration/test_stateless.py` 2케이스. 전체 104 passed.
  - **결정 — e2e 성공이 아니라 세션 id 부재로 단언한다.** e2e만 보면 토글을 무시하고 항상 stateful로 둬도 통과해서, 테스트가 토글을 검증하지 못한다. stateless 모드의 관측 가능한 차이는 서버가 `mcp-session-id`를 발급하지 않는 것이고 `streamablehttp_client`가 그 값을 `get_session_id()`로 노출한다. 가드 제거(`stateless=False` 하드코딩) 시 `assert 'e1dd005d...' is None`으로 실패하는 것을 확인 — T2·T3와 같은 절차.
  - **파싱은 `("1", "true")`만.** `yes`/`on`까지 받는 건 쓰지도 않을 유연성(CLAUDE.md Simplicity First). K8s env는 문자열이고 compose·매니페스트 둘 다 우리가 쓴다.
- [ ] **T1 (P1, CC ~1.5시간)** — 1주차 매니페스트 + k3d(agents ≥2, `--registry-create`) + Ingress 경유 e2e
  - **선행 블로커:** k3d 미설치 (2026-08-21 실측). helm은 2주 스코프에서 불필요 — 설치 보류. kubectl·docker는 Docker Desktop 번들로 있음.
  - **D6 — 6서비스 전부.** 08-15의 "Deployment×4"는 오기, ×6으로 정정. 빌드 이미지는 4개 그대로고 prom/grafana는 공식 이미지 + 설정 마운트(`observability/` 전부 합쳐 2.2KB).
  - **D7 — 이미지 반입은 k3d 내장 레지스트리.** `k3d image import` 아님(3노드 × 매 코드 변경마다 전체 복사). 30분 안에 배선이 안 풀리면 import로 폴백하고 findings에 기록.
  - **기록 의무:** `observability/prometheus.yml`의 `static_configs: ["gateway:8000"]`이 `replicas: 3`에서 임의 파드를 잡아 카운터가 튄다. **안 고친다** — ServiceMonitor는 3주차(D5 보류)이고 이 증상이 그 필요성의 실증이다. findings 여섯 번째 항목.
  - **기록 의무(신규, T3에서 발견):** **MCP 세션 핸들은 백엔드보다 오래 산다.** Streamable HTTP는 요청마다 POST라 유휴 중 백엔드가 죽어도 연결 소유 task가 `stop.wait()`에서 안 깨고, `upstream.py:64-65`의 `finally: self._session = None`이 안 돈다. 실측: 백엔드 kill 후 1초 뒤에도 `ensure_session()`이 **0.000초에 성공**해 죽은 세션을 그대로 반환한다. 설계 4B가 이걸 가정하지 못해, `ensure_session()`만 부르는 probe는 죽은 백엔드에 Ready를 준다. `send_ping()` 왕복을 얹어야 잡히고, 그 ping도 즉시 실패가 아니라 **2초 타임아웃까지 매달린다**(7A와 같은 성질). findings **일곱 번째** 항목 — K8s에서 "파드는 Ready인데 요청은 전부 실패"의 교과서 사례.
  - Files: `k8s/base/` (Deployment×6, Service×6, Ingress(gateway + grafana), ConfigMap(policy·prometheus·grafana), Secret, kustomization)
  - Verify: `GATEWAY_URL=http://<ingress>/mcp uv run python scripts/e2e_demo.py` → exit 0

### 2주차 — 네 가지를 하나씩 결론낸다

- [ ] **T5 (P1, CC ~1시간)** — 두 세션 전략 재현 + findings 초안
  - **Why (결정 1C):** 문제는 4개가 아니라 **2개의 결정**이다. `ratelimit.py:47` `allow(self, agent)`가 IP가 아닌 agent를 키로 쓰므로 sessionAffinity를 고르면 rate limit이 자동 해결되지만 3주차 KEDA가 자기모순이 된다(고정된 클라이언트가 스케일아웃을 못 나눠 받음). stateless는 그 반대.
  - Files: `k8s/overlays/`, `docs/k8s-stateful-findings.md`
- [ ] **T6 (P1, CC ~15분)** — ticket-server `replicas: 3` 조용한 소실 재현
  - **Why (결정 2B):** `servers/ticket/src/ticket_server/db.py:35` 파드 로컬 SQLite. 게이트웨이 4문제는 전부 시끄럽게 깨지는데(핸드셰이크 실패, 즉시 거부, 목록 불일치) **이것만 에러 없이 빈 결과.** 결론은 "replicas 1 고정 + 근거" — circuit breaker의 "파드별이 의미적으로 옳다"와는 **종류가 다른** 미수정 결론.
  - Files: `k8s/base/ticket-server.yaml`, `docs/k8s-stateful-findings.md`
- [ ] **T7 (P1, CC ~30분)** — `scripts/verify_scaleout.py` 증거 수집 스크립트 (결정 6A)
  - Verify: 한 줄 실행으로 rate limit 실효 한도 + ticket 소실 + 세션 전략 차이 출력 (`spike_concurrency.py` 패턴)
- [ ] **T9 (P1, CC ~1시간)** — findings 문서를 "두 결정" 구조로 완성
  - **이것이 이 트랙의 진짜 wedge다.** 채용담당자는 yaml을 읽지 않는다.
  - Verify: 성공기준 #2 — "고치지 않는다" 결론 2종(circuit breaker, ticket-server) 포함
- [ ] **T8 (P2, CC ~5분)** — `docs/architecture.md` 갱신 (201행 `/ready` 누락, 8장 폴더 지도에 `k8s/` 없음)
- [ ] **T10 (P2, CC ~20분)** — 커버리지 갭 12건 중 코드 경로 7건 (현재 승인된 결정 기준 2/14)

### 2주차 말 재결정 (D5)

- [ ] 3~4주차(OTLP/Tempo/kube-prometheus-stack/KEDA → ArgoCD 또는 kopf) 착수 여부를 **JD 5건 수집 결과 + 2주차 findings**로 결정. 자른 게 아니라 결정 시점을 증거 도착 지점으로 옮긴 것.
- [ ] **[미완] 아웃사이드 보이스** — Codex `-s read-only`, `model_reasoning_effort=high`가 5분 타임아웃. 재시도 시 `--enable web_search_cached` 빼고 10분. 물어볼 것: **"수요 증거 0인데 2주를 쓰는 게 맞나"**

---

## B. 정지 트랙 — Evidence Box 파일럿 (PARKED)

- [ ] **The Assignment 2차 대화 일정 잡기** — 이 트랙의 유일한 잠금 해제 조건.
  - **What:** 같은 담당자와 2차 대화. 수집 항목 4개: ① 현상 재구성(워크어라운드와 비용을 숫자로, **직함·원문 기록**) ② P3 조건 판정 ③ 파일럿 환경(HTTP냐 stdio냐 — stdio면 파일럿 컷 조정) ④ 파일럿 ask
  - **Why:** 설계는 8주간 구현 0줄이고, 구현 착수 조건 자체가 "일정이 잡힌 뒤"다. 일정이 없으면 이 트랙에 쓰는 시간은 전부 투기다.
  - **Status:** 수요 증거 1건(카테고리 확인)에서 정지. Kill criterion "재검토 착수" 칸.
- [ ] **compose audit 영속성 공백** (이 트랙 소유 — K8s 축 아님)
  - `docker-compose.yml` gateway 서비스에 `volumes:` 키가 없다(10-32행) + `.gitignore`에 `audit/`. 해시 체인의 대전제(단일 프로세스 + 영속 파일)가 **지금 compose에서도 성립 안 한다.** K8s가 깨뜨린 게 아니라 원래 없었다.

---

## C. 상시 — 트랙 무관

- [ ] **The Assignment (커리어) — JD 5건 수집** ⏳ **최우선**
  - **Why:** B 트랙의 수요 증거는 여전히 **0**. "K8s + vLLM 조합이 타깃 JD와 일치한다"는 미검증 가설이고, D5가 3~4주차를 여기에 묶어놨다. **1시간이면 2주짜리 결정을 검증한다.**
- [ ] **specs를 GitHub 이슈로 등록** ⏳ 스크립트 준비됨, gh 인증 대기
  - **Ready:** `scripts/specs_to_issues.sh` — 이슈 7개 생성 + 각 spec 메타에 역링크(멱등). `gh auth login` 후 `bash scripts/specs_to_issues.sh` 한 번.
  - **Blocker:** `gh` 미설치 + 인증은 수동(브라우저). 솔로 프로젝트라 필수 아님 — 레포 공개 전에 하면 충분.

---

## D. 미승인 제안 (승인되면 위 트랙으로 이동)

08-15 `/plan-eng-review`가 찾은 미승인 건 + 이후 발견분. 승인 전까지 착수하지 않는다.

1. **`ratelimit.py:42` ConfigMap 값 검증 없음** — `capacity = int(cap)`. "한도를 레플리카 수로 나눈다"는 10/3을 계산하게 만드는데 `int("3.33")`은 ValueError로 기동 실패. ConfigMap 세계에선 오타 = CrashLoopBackOff.
2. **`admin.py:49` audit 전체 파일 매 요청 메모리 로드** — `p.read_text()`. docstring의 "데모 규모에선 충분히 빠르다" 전제는 audit이 컨테이너와 함께 사라져서 성립했다. 3C에서 PVC를 붙이면 파일이 무한히 자라고 로테이션이 없다.
3. (compose audit 영속성 → B 트랙으로 배치 완료)
4. **`gateway/Dockerfile:6` — `COPY . .`가 `uv sync`보다 앞** (2026-08-21 D7 검토 중 발견). 소스 한 줄만 바뀌어도 의존성 레이어가 통째로 재빌드된다. 백엔드 3종 Dockerfile도 같은 패턴. compose에선 빌드가 드물어 안 드러났지만 T2·T3·T4는 매번 재빌드 → push → rollout이라 반복 비용이 D7보다 **여기 먼저** 걸린다. 고치면 `pyproject.toml`/`uv.lock`을 먼저 COPY하는 2단 구조.

---

## 완료

- ~~**The Assignment kill criterion 정의**~~ ✅ 2026-06-20 — `docs/design/agentops-gateway-design.md` § "Target User & Narrowest Wedge > Wedge Kill Criterion". 중단/재검토 착수/기본값 3기준. 2026-07-06 판정: **"재검토 착수"** 충족.
