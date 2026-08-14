---
status: handoff
branch: main
timestamp: 2026-08-14T11:56:00+09:00
files_modified:
  - docs/design/k8s-stateful-scale-out.md (신규 — K8s 도입 설계, APPROVED)
  - docs/design/evidence-box-pilot.md (신규 — 07-06 Evidence Box 설계 레포 사본)
  - docs/context/2026-08-14-context-save.md (본 문서)
---

## Working on: K8s 도입 설계 (/office-hours 완료) — 다른 환경에서 이어서 진행

### Summary

8주 만의 세션. 코드는 한 줄도 안 건드렸고, **K8s 도입 방향을 6가지로 탐색해 1개로 확정한
설계 세션**이었다. 결과물은 `docs/design/k8s-stateful-scale-out.md` (APPROVED).

핵심 발견 하나: **이 게이트웨이는 4중으로 stateful이라 `replicas: 3`을 주는 순간 조용히
정합성이 깨진다.** 이게 흔한 "Deployment yaml 썼습니다"보다 훨씬 강한 면접 자산이고,
사용자가 가져온 원래 로드맵에는 없던 항목이다.

`/plan-eng-review`를 이어서 돌리려다 scope gate에서 중단 — **다른 환경에서 재개 예정.**

---

### 이번 세션에서 확정된 결정 (D1–D4)

| # | 질문 | 결정 |
|---|------|------|
| **D1** | K8s는 어느 축을 위한 것인가 | **커리어 트랙 (별도 축)** — Evidence Box 파일럿은 현 보류 유지, compose 경로는 손대지 않음 |
| **D2** | 4주를 어떤 경로로 | **경로 1 = B(Stateful Scale-Out) + D(Day-2 Ops) 코어** |
| **D3** | 설계 문서 승인 | **승인** (Status: APPROVED) |
| **D4** | 다음 단계 | `/plan-eng-review` 실행 → **scope gate에서 중단, 미완료** |

**D1이 가장 중요하다.** 이 레포에는 서로 합쳐지지 않는 두 축이 있다:

- **wedge 축** — Evidence Box 감사 증거 어플라이언스. `docs/design/evidence-box-pilot.md`
  (2026-07-06 APPROVED). 실측 수요 증거 1건 확보. **구현 0줄, 8주째 정지.** 다음 관문은
  상대 회사 담당자와의 2차 대화이며 혼자 진행 불가.
- **커리어 축** — K8s stateful scale-out. `docs/design/k8s-stateful-scale-out.md`. 잡서치용.
  혼자 끝낼 수 있음.

K8s는 커리어 축에 기여하지만 wedge 축의 핵심 포지셔닝("클러스터 불필요, compose 한 줄,
30분 설치" = 도입 마찰 제로)을 **약화시킨다.** 그래서 K8s 작업은 `k8s/` 디렉터리에만 하고
**`docker-compose.yml` / `scripts/e2e_demo.py` / CI e2e 잡은 건드리지 않는다**는 제약이 걸렸다.

---

### 코드 실측으로 찾은 것 (이번 세션 최대 산출)

#### 1. `replicas: 3`이 깨뜨리는 네 가지

| 상태 | 위치 | 레플리카 3개면 |
|---|---|---|
| MCP 세션 | `gateway/src/gateway/app.py` — `StreamableHTTPSessionManager(app=server)`, `stateless=True` 미지정 | `mcp-session-id`가 다른 파드로 가면 세션 미존재. 핸드셰이크 파손 |
| audit 로그 | `gateway/src/gateway/audit.py` → 로컬 `audit.jsonl` | 파드마다 별도 파일. `/admin`은 자기 파드 것만 읽음. **Evidence Box의 해시 체인은 원리적으로 성립 불가** |
| rate limit | `gateway/src/gateway/ratelimit.py` `self._buckets: dict` | 파드당 독립 버킷 → 실효 한도가 **3배**. 정책이 조용히 위반됨 |
| circuit breaker | `gateway/src/gateway/circuit.py` `self._failures` / `_opened_at` | 파드마다 독립 학습 → `tools/list` 결과가 파드마다 다름 |

추가로: 정책은 `Policy.load`로 기동 시 1회만 로드(핫 리로드 없음) → ConfigMap 변경 시
rollout restart 필요. 백엔드 MCP 세션은 `upstream.py`가 백엔드당 1개 + 소유 task로 유지하므로
파드 수 × 백엔드 수만큼 업스트림 연결이 증식한다.

**네 개 중 circuit breaker는 "고치지 않는 게 맞다"는 결론이 정답일 가능성이 높다** —
파드마다 자기 연결의 건강을 판단하는 게 옳기 때문. 이 기각 논증이 findings 문서의 최대
차별점이 된다 (`docs/spikes/opa-rego-vs-yaml.md`에서 이미 한 번 발휘한 근육).

#### 2. OTLP 배선이 아예 없다

`gateway/src/gateway/observability.py`가 `ConsoleSpanExporter`로 **stdout에만** trace를
내보낸다. "OTel 계측했다"는 있지만 트레이스 파이프라인은 없는 상태. 3주차에 채울 순증 가치.

#### 3. Prometheus 타깃이 단일 static

`observability/prometheus.yml`이 `static_configs: ["gateway:8000"]` 하나뿐이라 레플리카가
늘면 그대로는 성립하지 않는다. → ServiceMonitor로 교체 필요.

---

### 사용자 원안 로드맵 검토 결과

원안: 1주 컨테이너화 배포 → 2주 Helm + OPA 분리 → 3주 kube-prometheus-stack + OTel →
4주 ArgoCD + KEDA + vLLM.

**살린 것**
- 기존 자산 레버리지(새 프로젝트 안 팜) — 근거까지 맞음
- k3d 선택 — 단 **워커 노드 2개 이상**(`--agents 2`) 조건 추가. 단일 노드면 RWX 볼륨 제약이
  안 드러나서 2주차 audit 문제가 반쪽이 된다
- graceful shutdown — 가장 좋은 지적. `lifespan` 종료와 진행 중 `tools/call`의 경합이
  코드만 봐선 확정 안 되므로 실측 대상
- 관측성 K8s 승격

**고친 것**
- ❌ **"OPA 별도 서비스로 분리"** → 제외. `docs/spikes/opa-rego-vs-yaml.md`가 "YAML 유지,
  OPA 미채택"으로 결론냈고, 그 메모가 "3×3 매트릭스에 사이드카는 판단력 부족 신호"라고
  직접 쓴다. 그대로 하면 면접관이 레포에서 자기모순을 발견한다
- ❌ **"KEDA 큐 깊이 기반"** → 이 시스템은 동기 요청/응답이라 큐가 없다. 이미 노출 중인
  Prometheus 메트릭(`TOOL_CALLS`, `CALL_DURATION`) 기반 스케일러로 교체
- ⚠️ **"startupProbe: vLLM 로딩 스토리"** → Qwen2.5-0.5B CPU로는 몇 초면 로드돼서 **재현 안 됨.**
  대신 이 코드의 진짜 문제 사용: `/health`가 무조건 `{"status":"ok"}` 반환 →
  "백엔드 0/3 연결된 게이트웨이는 ready인가?"
- ⚠️ **"resource limits: KV cache와 연결"** → 게이트웨이엔 KV cache 없음. 대신 실측 가능한 것:
  파드당 백엔드 3종 세션 상주 → 메모리가 레플리카 수 × 업스트림 연결 수로 증가
- ⚠️ **"vLLM 얹으면 완성"** → 아키텍처상 붙을 자리 없음(게이트웨이는 LLM 호출 안 함).
  별도 축으로 이월. 표준 `vllm/vllm-openai` 이미지는 CUDA 기반이라 CPU는 별도 경로 —
  착수 전 30분 검증, 막히면 llama.cpp/Ollama로 대체

**빠져 있던 것**: 위 "네 가지" 전부. 1주차가 `replicas: 1`로 끝나면 만날 기회 자체가 없다.

---

### 탐색한 6가지 방향 (전문은 설계 문서)

| | 방향 | 증명하는 것 | 기간 | 희소성 | 채택 |
|---|---|---|---|---|---|
| A | Lift-and-Shift | 컨테이너 → K8s 이관 | 1주 | 낮음 | 1주차로 흡수 |
| **B** | **Stateful Scale-Out** | **분산 시스템 디버깅** | 2주 | **높음** | **★ 코어** |
| C | Platform Engineering (Helm/ArgoCD) | 선언적 배포 | 1.5주 | 중간 | 4주차 후보 |
| **D** | **SRE / Day-2 Ops** | **운영·장애 대응** | 1.5주 | 중상 | **★ 3주차** |
| E | Operator / CRD (kopf) | K8s 확장 능력 | 2주 | **최상** | 4주차 후보 |
| F | LLM Serving (vLLM) | LLMOps | 1.5주 | 중간 | 기각·이월 |

E는 Evidence Box 스코프 5("`BACKEND_SPECS` 하드코딩 → YAML 설정 일반화")의 K8s-native
형태라 **두 축이 겹치는 유일한 지점** — 파일럿 재개 시 버려지지 않는다.

---

### 확정된 4주 배치

| 주차 | 내용 | 주말 산출물 |
|---|---|---|
| **1** | k3d(agents ≥2) + 매니페스트 6종 + probe 3종 + Ingress 경유 `e2e_demo.py` 통과 → **`replicas: 3`으로 깨뜨리기** | findings 초안 (재현 절차 + 증상) |
| **2** | 네 가지 진단·수정·기각 결론 | `docs/k8s-stateful-findings.md` 완성 — **핵심 산출물** |
| **3** | OTLP 실배선, ServiceMonitor, 파드 kill로 circuit breaker 실증, graceful shutdown, readiness 재정의, KEDA(Prom 스케일러) | Grafana 대시보드 + 장애 대응 기록 |
| **4** | C(Helm+ArgoCD) 또는 E(kopf 오퍼레이터) 택1 | 선언적 배포 또는 CRD |

**설계 근거 P6**: 이 프로젝트의 실측 이탈률은 100%다 (06-10 설계는 "Week 3가 통상 이탈
지점"이라 썼고, 07-06 설계는 8주간 0줄). 그래서 **1주차 말에 이미 제출 가능한 물건이 남는**
구조로 짰다. 4주 완주를 전제하지 않는다.

---

### 다음에 할 일

1. **[미완료 — 재개 지점] `/plan-eng-review`** — scope gate(D1: 리뷰 대상 확인)에서 중단됨.
   재개 시 대상은 `docs/design/k8s-stateful-scale-out.md`. 중점 검토 요청 4건:
   - `replicas=3` 네 가지 수정 방안이 실제로 성립하는지 — 특히 **Redis 없이 레플리카 간
     rate limit을 정확하게** 만드는 경로
   - k3d local-path가 **RWO만 제공**할 때 audit 공유의 실현 가능한 대안
   - 4주 배치가 솔로 + 잡서치 병행에서 현실적인지
   - compose 경로 무손상 제약이 실제로 지켜질 수 있는지

2. **[The Assignment — 코드보다 먼저] 타깃 포지션 JD 5건을 모아 요구사항을 세라.**
   현재 이 트랙의 수요 증거는 **0**이다. "LLMOps 성향 JD에서 요구하는 조합 그대로"는
   이번 세션에서 검증되지 않은 진술. 한 시간이면 4주짜리 결정을 검증한다.
   - K8s를 **필수**로 적은 곳 몇 곳 (우대/필수 구분)
   - **LLM 서빙**(vLLM/TGI)을 적은 곳 몇 곳 → Approach F 착수의 유일한 근거
   - **관측성**(OTel/Prometheus)을 적은 곳 몇 곳
   - **반복 등장하는데 이 레포에 없는 것** ← 가장 중요. K8s가 아닌 다른 것(Terraform, 특정
     클라우드 등)이면 4주 계획 자체를 재배치해야 한다

3. 구현 착수 (1주차부터)

---

### 기존 TODOS 상태 (변동 없음)

- ~~kill criterion 정의~~ ✅ 완료 (2026-06-20)
- specs를 GitHub 이슈로 등록 ⏳ `scripts/specs_to_issues.sh` 준비됨, `gh` 인증 대기

---

### 참고 파일 위치

| 무엇 | 어디 |
|---|---|
| K8s 설계 (본 세션) | `docs/design/k8s-stateful-scale-out.md` |
| Evidence Box 설계 (07-06) | `docs/design/evidence-box-pilot.md` |
| 원 설계 (06-10 + 개정) | `docs/design/agentops-gateway-design.md` |
| OPA 미채택 결정 | `docs/spikes/opa-rego-vs-yaml.md` |
| 아키텍처 도식 | `docs/architecture.md` |
| 기능별 학습 문서 10편 | `docs/learning/` |

**주의**: `~/.gstack/` 경로의 gstack 산출물은 `.gitignore`에 걸려 있어 레포에 없다. 위 설계
문서 2건은 이번에 레포로 복사한 사본이다 — 다른 환경에서는 이 레포 사본을 기준으로 볼 것.

### 상태

- 테스트: 97개 그린 (2026-06-20 기준, 이번 세션에서 코드 미변경이므로 유지)
- 워킹트리: 문서 3건 추가
- 코드 변경: **없음**
