# 학습 자료: `백엔드 MCP 서버 3종 (ticket·docs·ops)` 완전 해부

> 대상: `servers/{ticket,docs,ops}/src/**`
> 목적: Gateway가 라우팅·인증·정책을 거는 "대상"이자 권한 매트릭스의 열이 되는 세 백엔드가 — 외부 의존성 없이, 의도적으로 순진하게 — 어떻게 구현됐는지 줄 단위로 이해한다.
> 관련 스펙: docs/specs/02-backend-servers.md, docs/design/agentops-gateway-design.md

---

## 0. 큰 그림

```
                  ┌─────────────────────────────────────────────┐
   AI 에이전트  → │              Gateway (FastAPI)              │  ← JWT 인증 · YAML 정책 · audit
   (ticket/      │  서버별 prefix 라우팅 + tool-call 단위 강제   │     (통제는 전부 여기)
    analyst/     └───────┬───────────────┬───────────────┬──────┘
    support)            │               │               │
                ┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼────────┐
                │ ticket :8101 │ │ docs :8102   │ │ ops :8103    │
                │ (쓰기 있음)  │ │ (읽기 전용)  │ │ (민감 데이터)│
                ├──────────────┤ ├──────────────┤ ├──────────────┤
                │ create_ticket│ │ search_docs  │ │ get_metrics  │
                │ search_tickets│ │ read_doc     │ │ query_logs   │  ← S4 인자 정책의 표적
                │ update_status│ │              │ │              │
                ├──────────────┤ ├──────────────┤ ├──────────────┤
                │ SQLite       │ │ BM25(rank_   │ │ 결정적 가짜  │
                │ tickets.db   │ │ bm25) corpus │ │ 데이터 생성기│
                └──────────────┘ └──────────────┘ └──────────────┘
        세 서버 모두: FastMCP + Streamable HTTP + /health · 인증·정책 없음
```

세 백엔드는 **한 건물 안의 세 전문 창구**라고 보면 된다. 티켓 창구(ticket)는 민원을 접수하고 상태를 바꿔주는 — 즉 "쓰기"를 하는 — 유일한 곳이다. 자료실(docs)은 읽기만 가능한 무해한 사내 문서 보관소다. 운영실(ops)은 서버 메트릭과 로그를 다루는, 아무나 들여보내면 안 되는 민감한 부서다. 이 성격 차이가 곧 권한 매트릭스(3 에이전트 × 3 서버)의 "열"을 채운다: 쓰기 권한 칸은 ticket으로, 누구나 읽는 기준선은 docs로, 전면 차단·인자 제한 장면은 ops로 시연된다.

세 창구의 공통된 핵심 성격은 **순진함(naïveté)**이다. 어느 창구도 "당신 누구냐", "그건 보면 안 된다"를 따지지 않는다. 신분증 검사(인증)도, 출입 제한(정책)도 전혀 하지 않고 요청대로 응답한다. 이것은 버그가 아니라 설계다 — 통제 지점을 Gateway 한 곳에 모으는 것이 이 프로젝트의 요지이고, 백엔드가 순진할수록 "Gateway가 없으면 강제가 불가능하다"는 명제가 선명하게 증명된다. 특히 ops의 `query_logs`는 시간 범위(≤24h)를 **일부러 서버에서 제한하지 않아서**, S4 Gateway 정책이 그 제한을 거는 표적이 된다.

또 하나의 공통 제약은 **외부 의존성 제로의 완전 통제 시뮬레이션**이다(design.md, Week 1~3). 실제 DB 서버·검색 엔진·로그 수집기에 붙는 대신, 셋 다 자족적으로 데이터를 만들어 낸다: ticket은 파일 한 개짜리 SQLite, docs는 순수 파이썬 BM25, ops는 시드 고정 결정적 생성기. 덕분에 데모는 어디서든 추가 인프라 없이 뜨고, 같은 입력엔 항상 같은 출력이 나와 테스트가 값을 단언할 수 있다.

### 구조 매핑

| 서버 | 포트 | 성격 | tools | 데이터 소스 파일 |
|------|------|------|-------|------------------|
| ticket-server | :8101 | 쓰기 있는 정책 테스트용 | create_ticket · search_tickets · update_status | `ticket_server/db.py` (SQLite) |
| docs-server | :8102 | 읽기 전용 대표 (기준선) | search_docs · read_doc | `docs_server/search.py` (BM25) |
| ops-server | :8103 | 민감 데이터 (거부·인자 정책 무대) | get_metrics · query_logs | `ops_server/fake_data.py` (결정적 생성기) |

세 서버는 모두 `server.py`(FastMCP 인스턴스 + tool 데코레이터 + /health), 데이터 모듈, `__main__.py`(`mcp.run(transport="streamable-http")`)라는 **동일한 3-파일 구조**를 공유한다. 한 서버를 이해하면 나머지 둘의 골격은 그대로 따라온다.

---

## 1. ticket-server — 쓰기 있는 정책 테스트용

### 배경: 왜 SQLite·왜 쓰기 tool

3개 백엔드 중 `create`/`update` 같은 **쓰기 tool을 가진 유일한 서버**다. 권한 매트릭스에는 "읽기는 되지만 쓰기는 안 되는" 칸(예: analyst-agent는 ticket을 검색만 할 수 있고 생성·수정은 막힘)이 있는데, 그 칸을 실증하려면 쓰기 tool이 실재해야 한다. 그래서 의도적으로 쓰기를 둔다 — "tool마다 별도 권한이 필요하다"를 보여주는 표본이다.

저장소로 SQLite를 고른 이유는 design.md의 "외부 의존성 없는 통제 시뮬레이션" 제약이다. 별도 DB 서버를 띄우면 의존성·기동 복잡도가 늘지만, SQLite는 파일 하나면 끝난다. 게다가 **볼륨 마운트를 일부러 안 붙여** 재시작마다 데이터가 사라지게 했다 — 데모가 매번 깨끗한 상태에서 시작하는 편이 재현성에 유리하기 때문이다.

#### `db.py` — SQLite 저장소 줄별 해설

```python
VALID_STATUSES = {"open", "in_progress", "closed"}
```
상태값 화이트리스트. 임의 문자열로 티켓 상태가 오염되는 걸 막는 단 하나의 진실 공급원이다. 아래 `update_status`가 이 집합으로 입력을 검증한다.

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ...
);
"""
```
**멱등 스키마**다. `IF NOT EXISTS` 덕에 매 연결마다 실행해도 안전하다 — 별도 마이그레이션 도구 없이 서버가 스스로 부트스트랩한다(통제 시뮬레이션의 단순성 원칙).

```python
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ.get("TICKET_DB_PATH", "tickets.db"))
    conn.execute(_SCHEMA)
    return conn
```
연결을 열고 매번 스키마를 보장한다. 경로가 `TICKET_DB_PATH` env로 교체 가능한 이유는 **테스트가 임시 파일을 주입**하기 위해서다(코드 수정 없이 격리된 DB로 돌릴 수 있음). 연결 풀이 없고 매 호출 새 연결을 여는 단순 모델인데, MCP tool 호출 빈도가 낮은 데모라 연결 비용이 문제되지 않고 수명 관리 코드도 사라진다.

```python
def create_ticket(title: str, body: str) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (title, body, status, created_at) VALUES (?, ?, 'open', ?)",
            (title, body, datetime.now(timezone.utc).isoformat()),
        )
        return {"id": cur.lastrowid, "status": "open"}
```
`with _connect()`는 sqlite3 connection의 context manager — 블록이 정상 종료되면 **자동 커밋**된다. 사용자 입력 title/body를 문자열에 직접 끼우지 않고 파라미터 바인딩(`?`)을 써 **SQL 인젝션을 차단**한 점이 핵심이다. 새로 만든 행의 id는 `cur.lastrowid`로 돌려준다. 반환 형태는 스펙대로 `{"id", "status":"open"}`.

```python
def search_tickets(query: str) -> list[dict]:
    pattern = f"%{query}%"
    ...
    "SELECT id, title, status FROM tickets WHERE title LIKE ? OR body LIKE ?"
```
query를 `%...%`로 감싸 LIKE 부분 일치 검색을 한다. title 또는 body 둘 중 하나라도 substring이 맞으면 매치. 여기서도 패턴은 바인딩으로 넘긴다.

```python
def update_status(ticket_id: int, status: str) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}: must be one of {sorted(VALID_STATUSES)}")
    with _connect() as conn:
        cur = conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        if cur.rowcount == 0:
            raise ValueError(f"ticket {ticket_id} not found")
    return {"id": ticket_id, "status": status}
```
두 가지 검증이 들어간다: (1) 무효 status는 화이트리스트로 거절, (2) `cur.rowcount == 0`이면 해당 id의 티켓이 없다는 뜻이라 거절. **여기서 던지는 `ValueError`는 FastMCP가 tool result의 `isError`로 변환**해 에이전트에게 사람이 읽을 메시지로 전달한다 — 서버는 크래시하지 않는다. 중요한 구분: 이건 백엔드 "실행" 단계의 입력 검증 실패이지, Gateway 정책의 "권한" 거부와는 다른 층위다.

#### `server.py` — FastMCP tool 노출 줄별 해설

```python
mcp = FastMCP("ticket-server", host="0.0.0.0", port=8101)
```
**왜 FastMCP인가**: Gateway는 저수준 `Server`로 세밀히 제어하지만, 백엔드 3종은 "tool 몇 개를 그냥 노출"하는 게 전부라 보일러플레이트를 줄여주는 고수준 FastMCP를 쓴다. `@mcp.tool()` 데코레이터가 함수 시그니처·타입 힌트·docstring에서 **MCP tool 스키마를 자동 생성**한다. host `0.0.0.0` / port `8101`은 컨테이너 외부(Gateway)에서 붙을 수 있게 한 것으로, Gateway의 `BACKEND_TICKET_URL` 기본값(`:8101/mcp`)과 짝이다.

```python
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")
```
docker compose healthcheck용 평문 GET 엔드포인트(S5). MCP 프로토콜이 아닌 단순 HTTP라, 오케스트레이터가 컨테이너 생존을 가볍게 확인한다. 세 서버 모두 동일하게 갖는다.

```python
@mcp.tool()
def create_ticket(title: str, body: str) -> dict:
    """Create a new ticket. Returns its id and initial status ("open")."""
    return db.create_ticket(title, body)
```
주목할 점: **이 docstring은 단순 주석이 아니라 에이전트(LLM)가 읽는 tool 설명**이다. "무엇을 받고 무엇을 돌려주는지, 유효 값이 무엇인지"를 명세해야 LLM이 정확히 호출한다. server.py는 얇은 어댑터 층 — 실제 로직은 전부 `db.py`로 위임하고, 세 tool 모두 `db.*`를 한 줄로 호출할 뿐이다. `update_status`의 docstring은 유효 status까지 적어 LLM에게 허용값을 알려준다.

#### `__main__.py` — 실행 진입점

```python
from ticket_server.server import mcp
mcp.run(transport="streamable-http")
```
`uv run python -m ticket_server`로 띄우는 진입점. transport를 `"streamable-http"`로 **고정**한 이유는 Gateway가 `streamablehttp_client`로 붙기 때문 — 양쪽 transport가 일치해야 통신이 성립한다(design.md 스택). docs·ops의 `__main__.py`도 글자만 바뀐 동일 코드다.

---

## 2. docs-server — 읽기 전용 + BM25 검색

### 배경: 왜 읽기 전용·왜 BM25

모든 에이전트에게 '읽기'가 허용되는 무해한 사내 문서 저장소다. 쓰기 tool이 아예 없어서 권한 매트릭스에서 **"누구나 읽을 수 있는" 기준선(baseline)**이 된다(거부 장면은 주로 ops에서 나온다). 단순 grep으로도 검색은 되지만, 그러면 "그럴듯한 도구"로 보이지 않는다 — 그래서 랭킹이 있는 BM25를 붙여 의미 있는 검색 도구처럼 만들었다.

**왜 BM25인가**: BM25는 외부 서비스나 임베딩 모델 없이 순수 파이썬(`rank_bm25`)으로 동작하는 고전 랭킹 함수다. "외부 의존성 없음" 제약을 지키면서도 substring grep보다 나은 관련도 정렬을 준다 — 이 데모에 정확히 맞는 선택이다.

#### `search.py` — corpus 로딩 + BM25 줄별 해설

```python
_DEFAULT_CORPUS_DIR = Path(__file__).parents[2] / "corpus"

_corpus: dict[str, str] | None = None
_bm25: BM25Okapi | None = None
_doc_ids: list[str] = []
```
corpus 경로를 `__file__` 기준으로 잡아 **어느 작업 디렉터리에서 띄워도 깨지지 않게** 했다(`parents[2]`가 패키지 루트 → `corpus/`). 전역 변수 3개가 `None`인 상태는 "아직 안 읽음"의 신호로, 이걸로 **lazy 로드를 트리거**한다. corpus·인덱스를 모듈 전역에 한 번만 만들어 재사용하되, import만으로는 디스크를 안 읽게 해서 **테스트가 부수효과 없이 모듈을 들일 수 있게** 했다.

```python
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9가-힣]+", text.lower())
```
소문자화 후 영문/숫자/**한글(가-힣)** 연속을 토큰으로 뽑는다. 한글을 토큰 클래스에 넣은 건 corpus에 한국어 문서가 섞일 수 있어서다 — 기본 영어 토크나이저면 한글이 통째로 빠져 검색이 안 된다.

```python
def _load() -> None:
    global _corpus, _bm25, _doc_ids
    corpus_dir = Path(os.environ.get("DOCS_CORPUS_DIR", _DEFAULT_CORPUS_DIR))
    _corpus = {p.stem: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}
    _doc_ids = list(_corpus)
    _bm25 = BM25Okapi([_tokenize(_corpus[d]) for d in _doc_ids])
```
`corpus/`의 모든 `*.md`를 읽어 인덱스를 구축한다. 파일명 stem(확장자 뗀 이름)이 곧 `doc_id`다. `sorted`로 파일 순서를 고정한 점이 중요한데, 이로써 **BM25 인덱스가 결정적**이 되어 같은 입력이 항상 같은 점수를 낸다(테스트 재현성). `BM25Okapi`는 "토큰화된 문서들의 리스트"로 인덱스를 만들고, 이 리스트는 `_doc_ids` 순서와 1:1 정렬되어 나중에 점수 배열의 i번째가 `_doc_ids[i]`에 대응한다. corpus_dir 역시 env로 교체 가능(테스트용).

```python
def search_docs(query: str) -> list[dict]:
    if _bm25 is None:
        _load()
    scores = _bm25.get_scores(_tokenize(query))
    ranked = sorted(zip(_doc_ids, scores), key=lambda x: x[1], reverse=True)
    return [
        {"doc_id": doc_id, "score": float(score), "snippet": _corpus[doc_id][:160]}
        for doc_id, score in ranked[:5]
        if score > 0
    ]
```
첫 호출이면 `_load()`로 인덱스를 만든다(lazy). `get_scores`가 query에 대한 문서별 점수 배열(_doc_ids 순서)을 주면, 점수 내림차순으로 정렬해 **상위 5건**만 돌려준다. 두 가지 다듬기: (1) `snippet`은 본문 앞 **160자 미리보기**라 응답이 가볍다(전체는 `read_doc`로), (2) `if score > 0`로 양수 점수만 남긴다 — 매치가 전혀 없는 문서(점수 0)는 물론, 흔한 단어라 IDF가 음수가 된 문서(BM25는 음수 점수 가능)도 함께 뺀다.

```python
def read_doc(doc_id: str) -> dict:
    if _corpus is None:
        _load()
    if doc_id not in _corpus:
        raise ValueError(f"doc {doc_id!r} not found")
    return {"doc_id": doc_id, "content": _corpus[doc_id]}
```
doc_id로 전문을 반환. **없는 id는 `ValueError`** → FastMCP가 `isError:true` tool 오류로 변환(크래시 없음). ticket의 무효 status 처리와 같은 패턴 — 잘못된 입력은 사람이 읽을 메시지로 정중히 거절한다.

#### `server.py` / `__main__.py`

`server.py`는 ticket과 동일 골격: `FastMCP("docs-server", port=8102)`(Gateway `BACKEND_DOCS_URL`과 짝), `/health` 평문 GET, 그리고 `search_docs`·`read_doc` 두 tool이 `search.*`를 한 줄씩 호출한다. docstring은 각각 "top 5: doc_id, score, snippet", "full content by doc_id"로 에이전트에게 반환 형태를 알린다. `__main__.py`는 `mcp.run(transport="streamable-http")` — ticket과 글자만 다르다.

---

## 3. ops-server — "민감 데이터" 역할

### 배경: query_logs 시그니처가 S4 인자 정책의 전제

운영 메트릭·로그를 다루는, 권한 매트릭스에서 **가장 민감한 백엔드**다. support-agent에겐 ops 전체가 '차단'되는 칸이라 S6 거부 데모의 무대가 된다. 그리고 `query_logs`는 한 단계 더 나아간 **'인자 레벨 정책'의 대상**이다: analyst-agent에 한해 허용하되 시간 범위는 ≤24h로 제한한다.

**핵심 설계 결정 — 범위 제한은 여기 없다.** `query_logs`의 docstring이 `"No range limit server-side"`라고 못박은 것은 의도다. 시간 범위 제한은 백엔드가 아니라 **Gateway 정책(S4)이 강제**한다. 통제 지점을 Gateway 한 곳에 모으는 게 이 프로젝트의 요지이므로, 백엔드는 순진하게 요청대로 데이터를 만들어 주고 "누가 / 무엇을 / 얼마나" 볼 수 있나의 판단은 전부 Gateway가 한다. 즉 ops-server는 "Gateway가 없으면 아무 제한도 걸리지 않는다"를 증명하는 살아있는 반례다.

#### `fake_data.py` — 결정적 생성기 줄별 해설

```python
VALID_METRICS = {"cpu", "memory", "requests"}
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
```
metric 화이트리스트와 고정 기준 시각. **왜 결정적인가**: "통제 가능한 시뮬레이션" 제약상 실제 운영 DB·로그 수집기에 붙지 않고 코드가 데이터를 만든다. 이때 무작위가 매번 다르면 테스트가 값을 단언할 수 없으므로, 기준 시각·시드를 고정해 **같은 입력엔 항상 같은 출력**이 나오게 했다.

```python
_LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR"]
_COMPONENTS = ["gateway", "ticket-server", "docs-server", "ops-server", "auth"]
_MESSAGES = [...]
```
로그 합성용 어휘 풀. `INFO`를 3번 넣어 가중치를 준 게 디테일이다 — 실제 로그처럼 INFO가 다수, WARN/ERROR가 소수가 되어 '그럴듯함'이 올라간다.

```python
def get_metrics(metric: str) -> dict:
    if metric not in VALID_METRICS:
        raise ValueError(f"invalid metric {metric!r}: must be one of {sorted(VALID_METRICS)}")
    base = {"cpu": 40.0, "memory": 60.0, "requests": 200.0}[metric]
    points = [
        {
            "ts": (_BASE + timedelta(hours=i)).isoformat(),
            "value": round(base + 10 * math.sin(i / 3) + (i % 5), 2),
        }
        for i in range(24)
    ]
    return {"metric": metric, "points": points}
```
무효 metric은 화이트리스트로 거절(`ValueError` → isError). 유효하면 metric별 기준선(cpu 40 / memory 60 / requests 200)에서 출발해 **24시간치 시계열**(시간당 1포인트)을 만든다. 값 = 기준선 + 사인파(완만한 주기 변동) + `(i%5)`(작은 톱니). 무작위가 아니라 **i의 함수**라 호출마다 동일한, 그러나 평평하지 않고 살아있는 곡선이 나온다.

```python
def _parse_iso(value: str, name: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid ISO8601 for {name!r}: {value!r}") from None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
```
ISO8601 문자열을 datetime으로. **파싱 불가 입력은 사람이 읽을 메시지로 거절**(스펙 요구사항: 무효 ISO8601은 isError). tz가 없으면 UTC로 간주해, 뒤에서 naive/aware datetime을 비교할 때 터지는 오류를 예방한다.

```python
def query_logs(query: str, start: str, end: str) -> dict:
    start_ts = _parse_iso(start, "start")
    end_ts = _parse_iso(end, "end")
    if end_ts < start_ts:
        raise ValueError("end must not be before start")
    lines = []
    ts = start_ts.replace(minute=0, second=0, microsecond=0)
    while ts <= end_ts:
        rng = random.Random(int(ts.timestamp()))
        line = (
            f"{ts.isoformat()} {rng.choice(_LEVELS)} "
            f"[{rng.choice(_COMPONENTS)}] {rng.choice(_MESSAGES)}"
        )
        if query.lower() in line.lower():
            lines.append(line)
        ts += timedelta(hours=1)
    return {"lines": lines, "count": len(lines)}
```
이 함수가 ops-server 설계의 정수다. start/end를 파싱하고 `end < start` 같은 **무의미한 구간만** 백엔드 차원에서 거절한다. 정시 단위로 정렬한 뒤 시간당 1줄씩 합성하는데, 각 줄을 **'그 시각'을 시드로 한 RNG**(`random.Random(int(ts.timestamp()))`)로 만든다 — 같은 시각은 항상 같은 줄을 낳으므로, 겹치는 구간을 두 번 조회해도 동일한 줄이 나온다(결정성). query는 대소문자 무시 substring으로 필터.

**결정적으로 빠진 것**: `end - start ≤ 24h` 같은 범위 제한이 코드 어디에도 없다. 1년치를 요청하면 1년치를 순순히 만들어 준다. 이 "안 함"이 의도된 설계로, S4 Gateway 정책이 인자를 가로채 제한을 거는 정확한 표적이 된다.

#### `server.py` / `__main__.py`

`FastMCP("ops-server", port=8103)`(Gateway `BACKEND_OPS_URL`과 짝), `/health`, 그리고 `get_metrics`·`query_logs` 두 tool. `query_logs`의 docstring `"... No range limit server-side."`는 단순 설명이 아니라 **설계 의도의 선언**이다 — 에이전트에게도, 코드를 읽는 사람에게도 "범위 제한은 내 책임이 아니다"를 명시한다. `__main__.py`는 역시 `mcp.run(transport="streamable-http")`.

---

## 4. Live wiring: Gateway가 이 서버들을 어떻게 집계·라우팅하는가

세 서버는 각자 독립 포트(8101/8102/8103)에서 단독으로 뜨고, 자기들끼리는 서로를 모른다. 이들을 하나로 묶는 건 Gateway다.

- **prefix 라우팅**: Gateway는 각 백엔드 URL(env `BACKEND_TICKET_URL` / `BACKEND_DOCS_URL` / `BACKEND_OPS_URL`)을 알고 있고, 세 서버의 tool을 한데 모아 에이전트에게 노출한다. 같은 이름 충돌을 피하려고 서버별 prefix로 네임스페이스를 나눈다(예: ticket의 `create_ticket`, ops의 `query_logs` 식으로 어느 창구의 tool인지 구분). 각 `server.py`의 `port=` 값과 Gateway의 `BACKEND_*_URL` 기본값이 짝을 이루도록 맞춰져 있다(주석에 명시됨).
- **transport 정합**: 세 `__main__.py`가 모두 `streamable-http`로 서빙하는 이유가 여기서 드러난다 — Gateway가 `streamablehttp_client`로 붙기 때문에 백엔드도 같은 transport여야 한다.
- **query_logs가 S4 정책의 표적이 되는 지점**: 에이전트가 `query_logs(query, start, end)`를 호출하면 그 요청이 Gateway를 통과한다. 백엔드는 범위를 제한하지 않으므로, Gateway가 이 tool-call을 가로채 (1) 호출자가 analyst-agent인지, (2) `end - start ≤ 24h`인지를 검사한다. 둘 중 하나라도 어긋나면 백엔드에 닿기 전에 거부된다. 즉 `query_logs`의 시그니처(`start`/`end`를 인자로 받는다) 자체가 인자 레벨 정책이 걸릴 수 있게 만든 **전제**다.
- **인증·정책은 백엔드에 0줄**: 세 server.py 어디에도 토큰 검증·호출자 식별이 없다. 누가 호출하든 응답한다. 이 공백이 "Gateway 없이는 강제 불가"를 증명한다.

## 5. 관통하는 설계 원칙 요약

- **관심사 분리 — 백엔드는 제한하지 않는다.** ops의 `query_logs`가 범위 제한을 일부러 빼고(`"No range limit server-side"`), 세 서버 모두 인증·권한 코드가 0줄이다. 통제는 전부 Gateway(S4)의 몫이며, 이 순진함이 곧 Gateway의 가치를 증명한다.
- **통제 가능한 시뮬레이션 — 외부 의존성 제로.** 별도 DB 서버 대신 SQLite 파일, 임베딩 모델 대신 순수 파이썬 BM25, 실제 로그 수집기 대신 코드 생성기. 어디서든 추가 인프라 없이 뜬다(design.md Week 1~3 제약).
- **결정적 시드 — 같은 입력 → 같은 출력.** ops는 `_BASE` 시각과 시각 기반 RNG 시드, docs는 `sorted` 파일 순서로 인덱스를 고정한다. 테스트가 구체적 값을 단언할 수 있어야 하므로 재현성이 설계 제약이다.
- **isError 비크래시 검증.** 무효 status / 없는 ticket·doc / 파싱 불가 ISO8601 / 무효 metric은 모두 `ValueError`로 정중히 거절되고, FastMCP가 이를 tool result의 `isError:true` + 사람이 읽을 메시지로 변환한다. 서버는 절대 죽지 않는다.
- **얇은 어댑터 server.py + 두꺼운 데이터 모듈.** server.py는 FastMCP 인스턴스 구성과 tool 노출만 하고 실제 로직은 db/search/fake_data로 위임한다. tool의 docstring·타입 힌트는 곧 에이전트가 읽는 스키마라 명세 문서 역할까지 겸한다.
- **SQL 인젝션 방지 — 파라미터 바인딩.** ticket의 모든 쿼리가 사용자 입력을 문자열에 끼우지 않고 `?` 바인딩으로 넘긴다(쓰기가 있는 유일한 서버라 더 중요).
- **성격 차등이 곧 권한 매트릭스의 열.** ticket=쓰기(권한 칸 테스트), docs=읽기 전용(누구나 허용 기준선), ops=민감(전면 차단·인자 제한 무대). 세 서버의 의도적 성격 차이가 3×3 매트릭스의 시연 시나리오를 채운다.
