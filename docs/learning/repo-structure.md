# 학습 자료: `리포지토리 트리 구조` 완전 해부

> 대상: 레포 최상위 디렉터리 전체 (`gateway/`, `servers/`, `demo-agent/`, `tests/`, `docs/`, `policies/`, `observability/`, `scripts/`, 루트 설정 파일들)
> 목적: "왜 폴더를 이렇게 나눴나"를 비개발자도 따라올 수 있게 해부한다. 다른 학습 문서가 *코드 한 파일*을 줄별로 보는 것과 달리, 이 문서는 *트리 구조 자체*를 설계 결정으로 읽는다.
> 관련 스펙: `docs/specs/01-scaffold.md`, `docs/architecture.md`, `docs/design/agentops-gateway-design.md`

---

## 0. 큰 그림

```
AgentOps_Gateway/
├── pyproject.toml            ← uv workspace 루트(멤버 묶음 + dev 도구 + 공통 설정)
├── uv.lock                   ← 워크스페이스 전체의 단일 잠금 파일
├── docker-compose.yml        ← 6개 프로세스를 한 번에 기동(런타임 위상의 선언)
│
├── gateway/                  ▣ 배포 단위 1 — 본체 Gateway (:8000)
│   ├── pyproject.toml        ·  자기 의존성(fastapi·jwt·otel…), 자기 빌드
│   ├── Dockerfile            ·  자기 이미지
│   └── src/gateway/…         ·  src-layout 패키지
│
├── servers/                  ▣ 배포 단위 2~4 — 백엔드 MCP 서버 3종 (그룹 폴더)
│   ├── ticket/  (:8101)      ·  각자 pyproject + Dockerfile + src/<name>_server/
│   ├── docs/    (:8102)
│   └── ops/     (:8103)
│
├── demo-agent/               ▣ 배포 단위 5 — 클라이언트 데모(LLM). dev 전용
│   └── src/demo_agent/…
│
├── tests/                    ← 루트 1곳. unit/ + integration/ 로만 분리
├── docs/                     ← design·specs·learning·context·spikes (수명 다른 문서들)
├── policies/policy.yaml      ← 권한 매트릭스(코드 아닌 데이터)
├── observability/            ← prometheus.yml + grafana/ (코드 아닌 설정)
├── scripts/                  ← 토큰 발급·e2e·점검(런타임 아닌 도구)
└── certs/                    ← 데모 TLS 자산
```

이 트리를 관통하는 **한 문장**: *폴더 경계 = 따로 배포되는 프로세스 경계 = 따로 격리되는 의존성 경계*. `docker-compose.yml`이 띄우는 박스 하나하나(gateway, ticket, docs, ops, demo-agent)가 최상위 폴더 하나와 1:1로 대응하고, 그 폴더는 각자 `pyproject.toml`로 자기 의존성만 들고 자기 `Dockerfile`로 자기 이미지를 굽는다. 트리는 곧 `docs/architecture.md`의 박스 다이어그램을 디스크에 옮겨 적은 것이다.

(메타포: 이 레포는 **한 단지 안의 여러 독립 건물**이다. 단지(workspace) 정문·관리사무소(루트 `pyproject.toml`·`uv.lock`)는 하나지만, 건물(gateway/servers/demo-agent)마다 자기 출입증·전기 계량기(의존성)·준공 도면(Dockerfile)이 따로 있다. 한 건물을 헐거나 새로 올려도 옆 건물은 멀쩡하다.)

| 폴더 | 무엇 | 배포 단위인가 |
| --- | --- | --- |
| `gateway/` | 모든 호출이 지나는 길목. 프로젝트 본체 | ✅ 프로세스/이미지 1 |
| `servers/ticket·docs·ops/` | 도구를 제공하는 백엔드 MCP 3종 | ✅ 프로세스/이미지 3 |
| `demo-agent/` | Gateway에 붙는 클라이언트(유일한 실 LLM) | ✅ 프로세스(이미지는 dev) |
| `tests/` | unit + integration 전부 | ❌ 검증용 |
| `docs/` | 설계·스펙·학습·세션기록·스파이크 | ❌ 문서 |
| `policies/`·`observability/`·`scripts/`·`certs/` | 정책·관측·도구·인증 자산 | ❌ 코드 밖 설정/도구 |

---

## 1. 왜 monorepo(uv workspace)인가 — 한 레포, 여러 패키지

### 배경: 왜 이렇게 설계했나

이 시스템은 한 프로그램이 아니라 **함께 떠야 의미가 있는 5개 프로세스**다(gateway 1 + 백엔드 3 + 에이전트 1). 선택지는 셋이었다: (a) 전부 한 패키지에 욱여넣기, (b) 레포 5개로 쪼개기(polyrepo), (c) 한 레포 안에 여러 패키지(monorepo/workspace). 이 프로젝트는 (c) — **uv workspace**를 택했다.

### 해설

```toml
# 루트 pyproject.toml
[tool.uv.workspace]
members = ["gateway", "servers/*", "demo-agent"]

[tool.uv.sources]
agentops-gateway = { workspace = true }
ticket-server   = { workspace = true }
# … 나머지도 workspace = true
```

- **의미**: 루트 `pyproject.toml`이 하위 폴더들을 워크스페이스 **멤버**로 선언한다. `servers/*` 글롭 하나로 백엔드 3종이 자동 편입된다. `[tool.uv.sources]`의 `workspace = true`는 "이 의존성은 PyPI가 아니라 *옆 폴더*에서 가져와라"는 뜻이다.
- **왜? ((a) 단일 패키지를 버린 이유)**: 한 패키지로 합치면 gateway 프로덕션 이미지에 langgraph·rank-bm25까지 전부 끌려 들어온다(2장 참조). 책임이 다른 코드가 한 import 네임스페이스에 섞여 "누가 누구를 부르는지" 경계도 흐려진다.
- **왜? ((b) polyrepo를 버린 이유)**: 5개 레포로 쪼개면 한 번의 변경(예: MCP 버전 업)이 5개 PR·5개 잠금 파일로 흩어진다. 이 프로젝트는 다섯 조각이 **한 데모로 같이 진화**하므로, 원자적 커밋·단일 `uv.lock`·교차 패키지 통합 테스트(6장)가 가능한 monorepo가 맞다.
- **단일 `uv.lock`**: 워크스페이스 전체가 잠금 파일 하나를 공유한다 → 모든 프로세스가 *정확히 같은* mcp/httpx 버전을 쓴다(프로토콜 불일치 사고 차단).

(메타포: 워크스페이스는 **공유 부엌을 둔 셰어하우스**다. 입주자(패키지)는 방을 따로 쓰지만 장보기 목록(`uv.lock`)은 한 장으로 합쳐 사 와, 누구는 우유 2.0 누구는 2.1을 쓰는 일이 없다.)

---

## 2. 왜 패키지마다 `pyproject.toml` + 독립 의존성인가

### 배경: 왜 이렇게 설계했나

이 트리에서 **가장 값나가는 결정**이다. 각 멤버 폴더는 자기 `pyproject.toml`에 *자기가 실제로 쓰는 것만* 적는다. 그래서 배포 단위마다 의존성 표면이 다르다.

### 해설

```
gateway/      → fastapi, mcp, pyjwt, pyyaml, jinja2, opentelemetry, prometheus  (LLM 0)
servers/docs/ → mcp, rank-bm25                                                  (검색만)
servers/ticket/, servers/ops/ → mcp (+ 자기 데이터 의존성)                        (최소)
demo-agent/   → langgraph, langchain-anthropic, langchain-core, mcp, pyjwt       (LLM 스택)
```

- **의미**: gateway는 LLM 라이브러리를 **단 하나도** 의존하지 않는다. 무거운 langgraph/langchain은 오직 `demo-agent/`에만 있다. BM25 검색은 오직 `servers/docs/`에만 있다.
- **왜? (격리의 실익 — 프로덕션 이미지가 얇아진다)**: 루트 주석이 이걸 명시한다 —
  ```toml
  # demo-agent는 dev 그룹에만 — 테스트가 graph를 import하되 gateway 프로덕션
  # 이미지(uv sync --no-dev --package agentops-gateway)에는 langgraph가 섞이지 않는다.
  ```
  `uv sync --no-dev --package agentops-gateway`로 gateway만 설치하면 langgraph는 따라오지 않는다. 공격 표면·이미지 크기·빌드 시간이 다 줄고, "보안 게이트웨이 본체에 LLM SDK가 왜 들어있나"라는 의문 자체가 사라진다.
- **왜? (각자 빌드 백엔드)**: 멤버마다 `[build-system] hatchling` + `[tool.hatch.build.targets.wheel] packages = ["src/<pkg>"]`을 둬, 각 패키지가 **독립적으로 휠로 빌드**된다. 이게 패키지별 Dockerfile이 자기 것만 골라 담을 수 있는 전제다.

(메타포: 건물마다 **전기 계량기를 따로** 단 것이다. 본체 건물(gateway)이 옆 건물(demo-agent)의 난방비(langgraph)까지 떠안지 않는다 — 쓰는 만큼만, 자기 것만.)

---

## 3. 왜 src-layout(`src/<pkg>/`)인가

### 배경: 왜 이렇게 설계했나

모든 패키지가 코드를 `폴더/src/<패키지명>/` 깊이에 둔다(예: `gateway/src/gateway/app.py`). 코드를 `gateway/gateway/`에 바로 두는 flat layout 대신 한 겹을 더 팠다.

### 해설

- **의미**: import 가능한 코드는 `src/` 아래에만 있다. 패키지 루트(`gateway/`)에는 `pyproject.toml`·`Dockerfile` 같은 *패키징 관심사*만 남는다.
- **왜? (실수로 "설치 안 된 코드"를 import하는 사고 차단)**: flat layout에서는 레포 루트에서 테스트를 돌리면 *설치 여부와 무관하게* 옆에 있는 소스가 우연히 import된다. src-layout은 "설치해야만 import된다"를 강제해, CI가 검증하는 코드와 실제 패키징되는 코드가 **항상 일치**하게 만든다(빠진 파일·잘못된 `packages=` 설정이 테스트에서 바로 드러난다).
- **왜? (패키징 관심사와 코드의 분리)**: `Dockerfile`·`pyproject.toml`은 "어떻게 포장·배포되는가", `src/`는 "무엇을 하는가"다. 폴더 한 겹이 이 둘을 갈라, 포장 작업이 코드 폴더를 어지럽히지 않는다.

(메타포: src/는 **포장된 상품과 매대 전단지를 섞지 않는 창고 칸막이**다. 상품(코드)은 안쪽 선반(`src/`)에, 가격표·배송 라벨(`pyproject`·`Dockerfile`)은 칸막이 바깥에.)

---

## 4. 왜 `servers/*`를 한 부모 아래 3형제로 두었나

### 배경: 왜 이렇게 설계했나

백엔드 셋(ticket·docs·ops)은 각자 독립 배포 단위인데도 `servers/`라는 **공통 부모** 아래 묶였고, 내부 구조가 글자 그대로 동일하다.

### 해설

```
servers/
├── ticket/  src/ticket_server/{server,db}.py        :8101  쓰기 있음
├── docs/    src/docs_server/{server,search}.py       :8102  읽기 전용 + BM25
└── ops/     src/ops_server/{server,fake_data}.py     :8103  민감 데이터
```

- **의미**: 셋 다 `src/<name>_server/server.py`(MCP 정의) + 데이터/검색 모듈이라는 **같은 형틀**을 따른다. 다른 건 포트와 역할뿐이다.
- **왜? (권한 매트릭스의 "열"을 폴더로 표현)**: 이 셋은 임의의 백엔드가 아니라 정책 매트릭스를 시험하기 위한 **역할 표본**이다 — ticket=쓰기 가능(읽기O/쓰기X 칸 테스트), docs=읽기 전용 기준선, ops=전면 차단 대상(S6 거부 데모 무대). 한 부모로 묶으면 "이들은 같은 종류(라우팅 대상 백엔드)"라는 사실이 트리에서 바로 읽히고, 워크스페이스도 `servers/*` 글롭 한 줄로 셋을 잡는다.
- **왜? (동일 형틀)**: 4번째 백엔드 추가가 "기존 폴더 복사 → 이름·도구만 교체"로 끝난다. 형틀이 같아 학습 문서(`backend-servers.md`)도 셋을 한 번에 설명한다.

(메타포: `servers/`는 **같은 평면도로 지은 상가 3채**다. 한 채는 쓰기 창구가 있고 한 채는 열람만, 한 채는 출입이 까다롭다 — 용도는 달라도 골조가 같아, 넉 번째 점포도 같은 도면으로 금방 올린다.)

---

## 5. 왜 `demo-agent/`를 분리 + dev 그룹 전용으로 두었나

### 배경: 왜 이렇게 설계했나

demo-agent는 Gateway의 *일부*가 아니라 Gateway에 **붙는 손님(클라이언트)**이다. 그래서 gateway 옆 형제 폴더로 따로 서고, 루트의 `dev` 의존성 그룹에만 등록된다.

### 해설

```toml
# 루트 pyproject.toml
[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24", "ruff>=0.8", "demo-agent"]
```

- **의미**: demo-agent는 *프로덕션* 의존성이 아니라 *dev* 의존성이다. 테스트(`tests/unit/test_demo_agent.py`)는 graph를 import해 검증하지만, gateway 런타임 이미지에는 포함되지 않는다.
- **왜? (방향이 반대인 컴포넌트는 섞지 않는다)**: gateway는 요청을 *받고*, agent는 요청을 *보낸다*. 둘을 한 패키지로 합치면 "보안 게이트웨이가 자기에게 트래픽을 쏘는 LLM 에이전트를 품는" 이상한 의존이 생긴다. 클라이언트는 클라이언트 폴더에.
- **왜? (dev 전용이라 LLM 스택이 프로덕션에 안 샌다)**: 2장의 격리와 짝이다 — demo-agent를 dev 그룹에 둔 덕에 langgraph/langchain-anthropic이 gateway 프로덕션 sync 대상에서 빠진다. "데모는 풀스택으로 돌지만 배포 본체는 얇게"가 폴더 위치 하나로 달성된다.

(메타포: demo-agent는 **상점을 드나드는 단골 손님 마네킹**이다. 매장 운영 매뉴얼(프로덕션)에 손님을 적어 넣진 않지만, 영업 리허설(테스트·데모) 때는 반드시 세워 둔다.)

---

## 6. 왜 `tests/`를 루트 1곳에 두고 unit/integration으로 나눴나

### 배경: 왜 이렇게 설계했나

테스트를 각 패키지 안(`gateway/tests/` 등)에 흩지 않고 **루트 `tests/` 한 곳**에 모았고, 그 안을 `unit/`과 `integration/`으로만 갈랐다.

### 해설

```
tests/
├── unit/         패키지별 단위 — test_policy, test_auth, test_circuit, test_docs_server …
└── integration/  교차 패키지 — test_policy_matrix, test_metrics_tracing, test_smoke …
```

- **의미**: 단위 테스트는 모듈 하나를 격리 검증하고, 통합 테스트는 gateway+백엔드+(때로)agent를 **실제로 같이 띄워** 한 요청이 전 계층을 통과하는지 본다.
- **왜? (루트 집중)**: 통합 테스트는 본질적으로 **여러 패키지를 가로지른다** — 정책 매트릭스 검증은 gateway와 백엔드 3종을 동시에 알아야 한다. 어느 한 패키지 폴더 안에 둘 수 없는 테스트라, 워크스페이스 전체를 보는 루트가 자연스러운 자리다. `pytest`도 루트 `[tool.pytest.ini_options] testpaths = ["tests"]` 한 줄로 전부 수집한다.
- **왜? (unit/integration 2분할)**: 속도·격리 특성이 다르다. unit은 빠르고 외부 프로세스가 없으며, integration은 실제 포트·세션을 띄워 느리고 무겁다. 폴더로 갈라 두면 "빠른 것만" 따로 돌리거나 CI에서 단계를 나누기 쉽다.

(메타포: `tests/`는 단지 전체를 점검하는 **공용 시험동**이다. 방 하나만 보는 점검(unit)과 단지의 배관·전기를 한꺼번에 흘려보는 점검(integration)을 같은 건물 두 층에 나눠 둔 것.)

---

## 7. 왜 `docs/`를 design·specs·learning·context·spikes로 나눴나

### 배경: 왜 이렇게 설계했나

문서를 한 폴더에 쌓지 않고 **수명과 목적이 다른 다섯 종류**로 갈랐다. 각 하위 폴더는 프로젝트 시간선의 다른 국면을 담는다.

### 해설

```
docs/
├── design/    ← 착수 전 "왜·무엇"의 큰 그림(1장짜리 설계 헌법)
├── specs/     ← 스프린트별 실행 명세 00-epic, 01-scaffold … 06-langgraph-admin
├── learning/  ← 사후 "코드 줄별 해부"(이 폴더 — 비개발자용)
├── context/   ← 세션 저장본(작업 재개용 체크포인트)
└── spikes/    ← Day-1 리스크 검증 기록(opa-rego-vs-yaml 등)
```

- **의미**: design은 *시작 전*, specs는 *만드는 중*, learning은 *만든 후*, context는 *세션 사이*, spikes는 *불확실성을 미리 찔러본* 기록이다.
- **왜? (문서마다 독자·수명이 다르다)**: 한데 섞으면 "지금 이게 계획인가 결과인가 회고인가"가 흐려진다. 분리해 두면 설계 헌법(design)은 거의 안 바뀌고, specs는 스프린트마다 늘고, learning은 코드가 굳은 뒤 따라붙고, context는 휘발성으로 쌓인다 — 각자 다른 속도로 자라도 서로 안 부딪힌다.
- **왜? (spikes를 따로 남기나)**: "왜 OPA/Rego 대신 YAML 정책인가" 같은 *가지 않은 길*의 근거다. 결정의 결과(코드)만 보면 사라지는 맥락을, 검증 기록으로 남겨 나중에 "이미 따져봤다"를 증명한다.

(메타포: `docs/`는 한 건물의 **도면 캐비닛**이다. 설계 헌법(design)·시공 지시서(specs)·준공 후 사용설명서(learning)·작업일지(context)·지반 조사 보고서(spikes)를 같은 서랍에 안 섞고 칸을 나눠 보관한다.)

---

## 8. 왜 운영 자산을 코드 밖 최상위에 두나

### 배경: 왜 이렇게 설계했나

`policies/`, `observability/`, `scripts/`, `certs/`, `docker-compose.yml`은 어느 패키지의 `src/`에도 들어가지 않고 **레포 최상위**에 산다. 코드가 아니라 *데이터·설정·도구*이기 때문이다.

### 해설

| 자산 | 무엇 | 왜 코드 밖인가 |
| --- | --- | --- |
| `policies/policy.yaml` | 3에이전트×3서버 권한 매트릭스 | 권한은 **데이터**다. 코드 재배포 없이 정책만 바꿀 수 있어야 한다(`policy.py`가 경로를 env로 읽음). |
| `observability/` | prometheus.yml + grafana 대시보드/프로비저닝 | 관측 *설정*이지 게이트웨이 코드가 아니다. Prometheus·Grafana 컨테이너가 마운트해 읽는다. |
| `scripts/` | issue_tokens·e2e_demo·check_* | 런타임이 아니라 **운영 도구**다. 프로세스로 안 뜨므로 패키지가 아니다. |
| `certs/` | 데모 TLS 자산 | 환경 자산. 코드와 생명주기가 다르다. |
| `docker-compose.yml` | 6개 프로세스 기동 선언 | 런타임 *위상*의 선언. 어떤 패키지에도 속하지 않는 오케스트레이션 층. |

- **왜? (설정-as-데이터 분리)**: 같은 코드가 로컬·compose 양쪽에서 돌게 하려면, 환경마다 달라지는 것(URL·정책 경로·토큰)을 코드가 아니라 env+최상위 자산으로 빼야 한다. `app.py`의 `BACKEND_SPECS`가 기본 URL을 env로 덮어쓰는 것과 같은 철학이 폴더 수준으로 올라온 것이다.
- **왜? (compose가 최상위인 이유)**: `docker-compose.yml`은 트리의 박스들을 *실제로 함께 띄우는* 유일한 파일이다. 0장에서 말한 "트리 = 런타임 위상"을 코드로 닫는 마침표라, 어느 하위 폴더가 아니라 단지 정문(루트)에 둔다.

(메타포: 이 자산들은 건물 안 가구가 아니라 **단지 공용 설비**다 — 출입 규정표(policies), CCTV 관제실(observability), 관리도구 창고(scripts), 정문 경비 배치도(docker-compose). 건물(패키지) 안이 아니라 단지 마당에 둔다.)

---

## 9. 관통하는 설계 원칙 요약

- **트리 = 런타임 위상**: 최상위 폴더 하나 = `docker-compose.yml`이 띄우는 프로세스 하나 = 자기 `pyproject`·`Dockerfile`을 가진 배포 단위 하나. 디스크 구조가 아키텍처 다이어그램을 그대로 베낀다.
- **폴더 경계 = 의존성 경계**: 패키지마다 자기 의존성만 선언해, gateway 본체에 LLM SDK(langgraph)나 검색 라이브러리(bm25)가 섞이지 않는다. `--no-dev --package`로 얇은 프로덕션 이미지를 굽는다.
- **monorepo, polyrepo 아님**: 다섯 조각이 한 데모로 같이 진화하므로 단일 레포·단일 `uv.lock`·원자적 커밋·교차 패키지 통합 테스트를 얻는다. 단, 패키지 경계는 워크스페이스로 명확히 유지.
- **src-layout으로 "설치해야 import"**: 검증되는 코드와 패키징되는 코드를 강제로 일치시키고, 패키징 관심사(`Dockerfile`·`pyproject`)를 코드 폴더 밖으로 분리.
- **같은 종류는 한 부모로**: 백엔드 3종을 `servers/*` 아래 동일 형틀로 묶어, "이들은 같은 역할(라우팅 대상)"을 트리로 표현하고 4번째 추가를 복사 한 번으로 만든다.
- **클라이언트는 클라이언트 폴더에, dev 전용으로**: 요청을 보내는 demo-agent는 받는 gateway와 섞지 않는다. dev 그룹에 둬 데모는 풀스택, 배포는 얇게.
- **코드와 설정-as-데이터의 분리**: 권한·관측·도구·오케스트레이션은 어느 `src/`에도 안 들어가고 최상위에 둔다 — 코드 재배포 없이 환경만 바꿔 끼울 수 있게.

---

## 함께 보면 좋은 문서

- 컴포넌트 ↔ 코드 매핑: [`docs/architecture.md`](../architecture.md) (이 문서가 "왜 이 트리인가"라면, architecture는 "이 트리의 어느 파일이 무슨 컴포넌트인가")
- 설계 전체: [`docs/design/agentops-gateway-design.md`](../design/agentops-gateway-design.md)
- 스캐폴딩 스펙: [`docs/specs/01-scaffold.md`](../specs/01-scaffold.md)
- 기능별 코드 해부: 이 폴더의 [README.md](README.md)
