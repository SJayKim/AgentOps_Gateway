# 학습 자료: `Observability (OTel·Prometheus·Grafana)` 완전 해부

> 대상: gateway/src/gateway/observability.py, observability/ (prometheus·grafana)
> 목적: Gateway가 흘려보내는 모든 tool call을 "계기판처럼" 실시간으로 보여주고, 한 요청을 처음부터 끝까지 추적(trace)할 수 있게 하는 관측성 스택을 해부한다.
> 관련 스펙: docs/specs/05-observability.md, docs/design/agentops-gateway-design.md

---

## 0. 큰 그림

```
   ┌──────────────────────────────────────────────────────────────┐
   │  AI 에이전트 (ticket / docs / ops)                            │
   └───────────────┬──────────────────────────────────────────────┘
                   │  tools/call (JWT 첨부)
                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  Gateway (FastAPI, :8000)                                     │
   │   요청 수신 → 인증 → 정책 평가 → 백엔드 호출                  │  ← OTel span 4단계
   │        │                                                      │
   │        ├─ record_call(decision)  ──► Prometheus 메트릭 3종    │
   │        └─ audit.record(trace_id) ──► audit JSONL (같은 사실)  │
   │                                                               │
   │   GET /metrics  ◄────── 텍스트로 메트릭 노출                  │
   └───────────────┬──────────────────────────────────────────────┘
                   │  5초마다 스크레이프(scrape)
                   ▼
   ┌──────────────────────┐        쿼리(PromQL)      ┌─────────────────┐
   │  Prometheus (:9090)  │ ◄──────────────────────  │  Grafana (:3000)│
   │  메트릭 시계열 저장   │ ──────────────────────►  │  대시보드 3패널 │
   └──────────────────────┘                          └─────────────────┘
```

**비유로 먼저.** 관측성(observability)은 자동차의 **계기판 + 블랙박스**다. 게이트웨이를 통과하는 모든 도구 호출은 자동차가 달린 거리와 같아서, 그냥 흘려보내면 흔적 없이 사라진다. 이 스택은 두 가지를 동시에 한다. 하나는 **계기판** — 지금 몇 건이 거부됐고, 누가 얼마나 호출하고, 응답이 얼마나 느린지를 숫자로 보여주는 Prometheus 메트릭이다. 다른 하나는 **블랙박스(CCTV)** — 사고가 났을 때 "그 요청 하나"가 인증→정책→백엔드를 어떻게 지나갔는지 끝까지 되돌려 보는 OpenTelemetry trace다.

**왜 이게 이 프로젝트의 차별점인가.** `observability.py` 첫 문단이 직접 밝힌다: 사용자가 4주 풀플랜(Approach B)을 고른 이유 자체가 "관측성 스택 딥다이브"였다(design.md). 그래서 **정책 거부 카운트 메트릭(`gateway_policy_denied_total`)은 "절대 빼지 않는" 핵심 산출물**로 못 박혀 있다. 권한 매트릭스(3 에이전트 × 3 서버)를 강제하는 게 이 게이트웨이의 핵심 가치이므로, "정책이 실제로 몇 건을 막았는가"는 가장 중요한 지표가 된다.

**핵심 설계 한 줄.** 메트릭과 audit은 **같은 호출 지점에서, 같은 decision 어휘로** 기록된다. 계기판의 숫자와 블랙박스의 기록이 어긋나면 안 되기 때문이다 — 둘이 같은 사실을 보게 만든 것이다.

| 파일 | 역할 (한 줄) |
|---|---|
| `gateway/src/gateway/observability.py` | 메트릭 3종 정의 + OTel tracer 설치 + `/metrics` 페이로드 — 데이터를 **만드는** 쪽 |
| `observability/prometheus.yml` | Prometheus가 Gateway `/metrics`를 5초마다 긁어오게 하는 스크레이프 설정 |
| `observability/grafana/provisioning/datasources/prometheus.yml` | Grafana가 어느 데이터 소스(Prometheus)를 볼지 코드로 지정 |
| `observability/grafana/provisioning/dashboards/dashboards.yml` | 대시보드 JSON을 자동 로드하는 provider 설정 |
| `observability/grafana/dashboards/gateway.json` | 패널 3개(거부 카운트·호출량·latency)의 실제 정의 — 데이터를 **보는** 쪽 |

---

## 1. `observability.py` — 메트릭·trace 설정 (코드)

### 배경

이 모듈은 관측성 스택의 **생산자(producer)** 다. Prometheus·Grafana는 컨테이너로 따로 돌지만, 그들이 긁어갈 숫자를 만드는 것은 결국 이 파이썬 파일이다. 두 가지를 제공한다: ① `prometheus-client`로 만든 **메트릭 3종**, ② OpenTelemetry **tracer**. 그리고 둘을 라우팅 경로에 연결하는 작은 함수들(`record_call`, `trace_id_hex`, `metrics_payload`).

### 메트릭 3종 — 무엇을 왜 세는가

```python
TOOL_CALLS = Counter(
    "gateway_tool_calls_total",
    "tools/call count by final decision",
    ["agent", "server", "tool", "decision"],  # decision: allowed|denied|auth_failed|error
)
```

**무엇.** 모든 `tools/call`을 **최종 decision별로** 세는 Counter(누적 증가만 하는 수치). 라벨은 `agent×server×tool×decision` 4개.
**왜 tool까지 라벨에 두는가.** Prometheus에서 라벨 조합 수(카디널리티)가 폭발하면 메모리·성능이 무너진다. 보통 `tool`처럼 값이 많아질 수 있는 라벨은 피한다. 하지만 주석이 밝히듯 데모 규모는 `3×3×~3×4`로 작아서(약 100여 조합) 폭발하지 않는다 — 그래서 안심하고 tool까지 라벨에 둘 수 있다. 작은 시스템이라 누릴 수 있는 호사다.

```python
CALL_DURATION = Histogram(
    "gateway_tool_call_duration_seconds",
    "tools/call routing+relay duration (p50/p99 도출용)",
    ["server", "tool"],
)
```

**무엇.** 라우팅+중계에 걸린 시간을 담는 Histogram(값을 구간별 버킷에 나눠 담는 분포 자료). 여기서 p50(중앙값)·p99(상위 1% 느린 호출)를 Grafana가 계산한다.
**왜 여기엔 `agent` 라벨이 없는가.** 주석: latency는 "**어느 백엔드/tool이 느린가**"의 문제지 "누가 불렀나"의 문제가 아니다. 호출자가 누구든 같은 tool은 비슷한 시간이 걸린다. agent를 빼면 의미도 정확해지고 카디널리티도 줄어든다 — 일석이조.

```python
POLICY_DENIED = Counter(
    "gateway_policy_denied_total",
    "policy-denied tools/call count — 핵심 메트릭, 절대 미삭제",
    ["agent", "server", "tool"],
)
```

**무엇.** 정책에 의해 거부된 호출만 세는 전용 Counter.
**왜 따로 두는가 — 중복 아닌가?** 맞다. `TOOL_CALLS`에서 `decision="denied"`로도 같은 숫자를 구할 수 있다. 그런데도 독립 메트릭으로 둔 이유는 **의지의 표현**이다. 주석이 직접 말한다: 이것이 design.md 전제 4의 "핵심 산출물"이라, 대시보드·알림에서 **1급 시민**으로 다루기 위해 따로 둔다. "절대 미삭제"라는 주석은 그 우선순위를 코드에 박아둔 것이다. (스펙 우선순위: **정책 거부 카운트 메트릭 > 대시보드 > stretch**.)

### tracer — 블랙박스를 한 번만 설치한다

```python
_provider_installed = False  # tracer provider 1회 설치 가드

def tracer() -> trace.Tracer:
    """게이트웨이용 OTel Tracer를 반환. 최초 호출 시 provider를 lazy 설치한다."""
    global _provider_installed
    if not _provider_installed:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _provider_installed = True
    return trace.get_tracer("gateway")
```

**무엇.** OpenTelemetry의 trace 생성기(tracer)를 돌려준다. 최초 호출 때만 전역 provider(span을 만들고 내보내는 엔진)를 설치한다.
**왜 전역 플래그로 1회만?** 모듈 docstring이 근거다: 테스트가 `build_app()`을 **반복 호출**한다. provider를 매번 새로 설치하면 OTel이 중복 설치 경고를 내거나 span이 엉킨다. 그래서 `_provider_installed` 전역 플래그로 최초 1회만 설치하는 가드를 둔다.
**왜 `SimpleSpanProcessor` + `ConsoleSpanExporter`인가?** exporter는 스펙대로 **콘솔 기본**이다(span을 표준출력에 찍는다). 그리고 Batch(비동기)가 아닌 **Simple(동기)** processor를 쓴다 — 주석이 이유를 정확히 밝힌다: Batch는 백그라운드 스레드에서 `atexit` 시점에 flush하는데, 그땐 테스트가 stdout을 이미 닫아 ConsoleExporter가 깨진다. 동기 export는 데모엔 충분하고 테스트도 안 깨뜨린다. **"데모에 맞는 단순함"** 의 전형이다.

### trace ID를 32 hex로 — audit과 공유하는 열쇠

```python
def trace_id_hex(span: trace.Span) -> str:
    """현재 span의 trace ID를 32자리 hex 문자열로. audit JSONL·게이트웨이 로그가 공유하는 키."""
    return format(span.get_span_context().trace_id, "032x")
```

**무엇/왜.** OTel이 부여한 trace ID(128비트 정수)를 **32자리 16진 문자열**로 변환한다. 스펙 요구사항이 "audit의 trace_id를 OTel trace ID(32 hex)로 교체"인데, 이 함수가 바로 그 변환점이다. 이 한 줄 덕분에 **계기판(메트릭)·블랙박스(trace)·기록부(audit JSONL)가 같은 trace ID라는 열쇠**로 한 요청을 가리키게 된다. 어떤 audit 줄을 보고 그 trace ID로 콘솔 로그를 grep하면 같은 요청의 span을 찾을 수 있다.

### record_call — 메트릭을 한 곳에서 기록

```python
def record_call(
    *, agent: str, server: str, tool: str, decision: str, duration_s: float | None = None
) -> None:
    """호출 1건의 메트릭을 한 곳에서 기록한다 (app.py가 audit.record와 나란히 호출)."""
    TOOL_CALLS.labels(agent=agent, server=server, tool=tool, decision=decision).inc()
    if decision == "denied":
        POLICY_DENIED.labels(agent=agent, server=server, tool=tool).inc()
    if duration_s is not None:
        CALL_DURATION.labels(server=server, tool=tool).observe(duration_s)
```

**무엇.** 호출 1건에 대한 메트릭 3종을 한 함수에서 갱신한다. ① 무조건 `TOOL_CALLS` +1, ② decision이 `denied`면 `POLICY_DENIED`도 +1, ③ 시간이 측정됐으면(`duration_s is not None`) `CALL_DURATION`에 관측.
**왜 `duration_s`가 None일 수 있나.** 주석: 인증 실패 등 **백엔드까지 못 간 호출**은 시간이 없다. 그런 호출의 0초/없음을 latency 통계에 넣으면 분포가 오염된다. 그래서 측정된 경우에만 `observe`한다 — **"맞는 숫자만 latency에 넣는다."**
**왜 "한 곳"인가.** 이 함수가 `app.py`에서 `audit.record`와 **나란히** 불린다(아래 N장). decision이 확정되는 **단일 지점**에서 메트릭과 audit이 함께 기록되므로 둘이 어긋날 수 없다.

### metrics_payload — Prometheus가 받아갈 텍스트

```python
def metrics_payload() -> tuple[bytes, str]:
    """(/metrics 응답 body, content-type). Prometheus 스크레이프가 그대로 받아 파싱한다."""
    return generate_latest(), CONTENT_TYPE_LATEST
```

**무엇/왜.** `prometheus-client`의 `generate_latest()`가 현재까지 누적된 모든 메트릭을 Prometheus 텍스트 포맷으로 직렬화하고, 표준 content-type을 함께 돌려준다. Gateway의 `GET /metrics` 핸들러가 이걸 그대로 응답하면, Prometheus가 5초마다 긁어가 파싱한다. **계기판으로 숫자를 내보내는 창구**다.

---

## 2. `prometheus.yml` — 스크레이프 설정

```yaml
# Gateway /metrics 스크레이프 — interval 5s (S5 P2)
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets: ["gateway:8000"]
```

**무엇.** Prometheus에게 "어디를 얼마나 자주 긁을지" 알려주는 설정.
- `scrape_interval: 5s` — **5초마다** 한 번 메트릭을 수집한다. 데모에서 대시보드가 거의 실시간으로 반응하게 하는 빠른 주기다.
- `job_name: gateway` / `targets: ["gateway:8000"]` — `gateway`라는 호스트의 8000 포트(`/metrics`는 기본 경로)를 대상으로 잡는다.
**왜 `gateway`라는 이름으로?** localhost가 아니라 `gateway`인 것은 docker compose 네트워크 안에서 **서비스 이름이 곧 호스트 이름**이 되기 때문이다. Prometheus 컨테이너가 같은 네트워크의 gateway 컨테이너를 이름으로 찾아간다. 매우 단순한 정적 타깃 — 데모에 서비스 디스커버리 같은 복잡한 장치는 필요 없다.

---

## 3. grafana provisioning + gateway.json 패널 구조

이 세 파일의 공통 목적은 **"수동 클릭 금지, 코드화(provisioning)"** 다. 스펙 요구: Grafana를 사람이 UI에서 클릭해 설정하면 안 되고, `docker compose up`만으로 데이터 소스와 대시보드가 떠야 한다. 설정을 코드로 박아두면 누가 띄워도 같은 화면이 재현된다 — **재현 가능성(reproducibility)** 의 실현이다.

### 3-1. datasource — Grafana가 볼 곳

```yaml
# 코드화된 datasource — 수동 클릭 설정 금지 (S5 P2)
apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

**핵심 줄과 의미.**
- `uid: prometheus` — 데이터 소스에 고정 식별자를 준다. **이 uid가 중요한 이유**: 대시보드 JSON(`gateway.json`)이 각 패널에서 `"datasource": { "uid": "prometheus" }`로 이 uid를 직접 가리킨다. uid가 일치하지 않으면 패널이 "데이터 소스 없음"으로 깨진다. 둘을 같은 문자열로 코드에 박아 연결을 보장한 것.
- `url: http://prometheus:9090` — 위 prometheus.yml과 같은 논리로, 컨테이너 서비스 이름 `prometheus`의 9090 포트를 가리킨다.
- `isDefault: true` — 기본 데이터 소스로 지정.

### 3-2. dashboards provider — 대시보드 자동 로드

```yaml
# 대시보드 JSON 자동 로드 — docker compose up 만으로 대시보드가 뜬다 (S5 P2)
apiVersion: 1

providers:
  - name: gateway
    folder: ""
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

**핵심 줄과 의미.**
- `type: file` / `path: /var/lib/grafana/dashboards` — 그 디렉터리에 든 모든 대시보드 JSON을 Grafana가 시작 시 자동으로 읽어들인다. `gateway.json`이 (볼륨 마운트로) 이 경로에 놓이면 손 하나 안 대고 대시보드가 뜬다. **"compose up = 대시보드 등장"** 을 만드는 장치.

### 3-3. gateway.json — 패널 3개 구조

대시보드 메타: `"uid": "agentops-gateway"`, `"refresh": "5s"`(5초마다 새로고침, 스크레이프 주기와 맞춤), `"time": now-15m → now`. 패널은 스펙이 요구한 정확히 3개다.

**패널 ① 정책 거부 카운트 (agent별)** — 핵심 패널.
```json
"title": "정책 거부 카운트 (agent별)",
"expr": "sum by (agent) (increase(gateway_policy_denied_total[5m]))",
"legendFormat": "{{agent}}"
```
`increase(...[5m])`는 최근 5분간 카운터가 얼마나 늘었는지(거부 건수의 증가분), `sum by (agent)`로 에이전트별 합산. **어느 에이전트가 권한 없는 도구를 얼마나 두드리는지**를 보여준다 — 권한 매트릭스가 실제로 일하고 있다는 증거판. 1순위 산출물인 `gateway_policy_denied_total`을 직접 쓴다.

**패널 ② 클라이언트별 호출량 (calls/s)**
```json
"title": "클라이언트별 호출량 (calls/s)",
"expr": "sum by (agent) (rate(gateway_tool_calls_total[1m]))",
"legendFormat": "{{agent}}"
```
`rate(...[1m])`는 초당 호출 속도, `sum by (agent)`로 에이전트별. **누가 게이트웨이를 얼마나 쓰는지**(트래픽 분포)를 본다.

**패널 ③ Latency p50/p99 (tool별)**
```json
"title": "Latency p50/p99 (tool별)",
"unit": "s",
"expr(A)": "histogram_quantile(0.5,  sum by (le, tool) (rate(gateway_tool_call_duration_seconds_bucket[5m])))",  // p50
"expr(B)": "histogram_quantile(0.99, sum by (le, tool) (rate(gateway_tool_call_duration_seconds_bucket[5m])))"   // p99
```
Histogram의 `_bucket` 시계열에 `histogram_quantile`을 씌워 **중앙값(p50)** 과 **꼬리 지연(p99)** 을 tool별로 뽑는다. `sum by (le, tool)`에서 `le`(버킷 경계)를 유지하는 게 분위수 계산의 필수 조건이다. p99가 튀면 그 tool/백엔드가 가끔 느리다는 신호 — **"평균은 멀쩡한데 일부 사용자가 느린"** 문제를 잡는 표준 기법이다.

---

## 4. Live wiring — 메트릭·trace가 라우팅·audit과 만나는 지점

이 모듈은 혼자 동작하지 않는다. 라우팅 경로(`app.py`의 `call_tool`)가 호출해야 비로소 숫자가 쌓인다. 담당 파일(observability.py)의 주석과 스펙이 그 연결을 명시한다:

- **OTel span 4단계** — 한 요청의 생애를 `요청 수신 → 인증 → 정책 평가 → 백엔드 호출` 네 단계 span으로 감싼다(스펙). `tracer()`가 만든 tracer로 이 span들을 연다.
- **trace_id 전파** — `trace_id_hex(span)`로 뽑은 32 hex가 백엔드 호출·**audit JSONL**까지 전파된다. audit의 `trace_id` 필드가 이 OTel trace ID로 채워진다(스펙: "audit의 trace_id를 OTel trace ID로 교체").
- **decision 단일 지점** — `record_call(...)`가 `app.py`에서 `audit.record(...)`와 **나란히** 불린다(`record_call` docstring). 즉 **decision이 확정되는 한 지점**에서 메트릭과 audit이 같이 기록된다.
- **decision enum 일치** — 메트릭의 `decision`은 `allowed | denied | auth_failed | error` 4종으로, audit이 기록하는 decision 어휘와 동일하다(모듈 docstring: "같은 decision enum 4종"). 그래서 **대시보드의 거부 카운트와 audit 로그의 거부 줄 수가 정확히 일치**한다 — 계기판과 기록부가 서로를 검증한다.

요컨대: 한 번의 `tools/call` → span 4단계로 추적 + `record_call`로 메트릭 3종 갱신 + `audit.record`로 같은 trace_id·같은 decision 기록. 세 시스템이 **한 사건의 세 얼굴**을 본다.

---

## 5. 관통하는 설계 원칙 요약

- **코드화된 provisioning (수동 클릭 금지)**: datasource·dashboard·스크레이프가 전부 YAML/JSON으로 박혀 있어 `docker compose up`만으로 동일한 대시보드가 재현된다. UI 클릭 설정은 사라지면 끝이지만, 코드는 git에 남는다.
- **메트릭·audit이 같은 사실을 보게**: 둘이 `app.py`의 같은 호출 지점에서 같은 `decision` 4종 어휘로 기록되어, 계기판 숫자와 기록부 줄 수가 절대 어긋나지 않는다.
- **정책 거부 카운트 우선 (절대 미삭제)**: `gateway_policy_denied_total`은 `TOOL_CALLS`로 대체 가능함에도 1급 메트릭으로 독립시켜, 게이트웨이의 핵심 가치(권한 매트릭스 강제)를 대시보드 첫 패널에 박았다.
- **trace ID 한 개로 세 시스템을 꿰기**: 32 hex OTel trace ID를 메트릭·trace·audit이 공유해, 한 요청을 처음부터 끝까지 되짚을 수 있다.
- **카디널리티를 의식한 라벨 선택**: tool 라벨은 데모 규모라 허용하고, latency에선 의미상·성능상 불필요한 agent를 뺐다 — 라벨은 "공짜"가 아니라는 자각.
- **맞는 숫자만 통계에 넣기**: 백엔드까지 못 간 호출(duration None)은 latency 히스토그램에서 제외해 p50/p99를 오염시키지 않는다.
- **데모에 맞는 단순함**: 동기 SpanProcessor·콘솔 exporter·정적 스크레이프 타깃·전역 1회 설치 가드 — 화려함 대신 "테스트를 안 깨고 compose 한 번이면 뜨는" 실용을 택했다.
