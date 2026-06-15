# 학습 자료: `Audit Admin 페이지` 완전 해부
> 대상: gateway/src/gateway/admin.py, gateway/src/gateway/templates/admin.html
> 목적: append-only audit JSONL 파일을 "지난 24시간 누가 민감 tool 접근을 시도했나"라는 한 질문에 답하는 사람용 거버넌스 리포트로 바꾸는 서버사이드 렌더링 페이지를, 비개발자도 줄 단위로 따라올 수 있게 해부한다.
> 관련 스펙: docs/specs/06-langgraph-admin.md, docs/design/agentops-gateway-design.md

---

## 0. 큰 그림

```
   브라우저
      │  GET /admin?token=SECRET   (최초 1회만 토큰을 URL에 실어 보냄)
      ▼
 ┌──────────────────────────────────────────────┐
 │  admin.py : @app.get("/admin")               │
 │                                              │
 │  1) 쿠키 검사 ── 있고 일치? ─► 통과           │
 │       │ 없음/불일치                           │
 │       ▼                                       │
 │  2) ?token= 검사 ── ADMIN_TOKEN 일치? ─► 통과 │
 │       │ 불일치/없음                           │
 │       ▼                                       │
 │     403 forbidden  (여기서 끝)               │
 │                                              │
 │  3) read_audit(audit_path)  ◄── audit.jsonl  │  ← DB 아님. 파일 직독
 │  4) summarize_denials()  → 상단 거부 요약표   │
 │     apply_filters()      → 하단 상세 테이블   │
 │  5) admin.html 렌더 + (최초면) 쿠키 심기      │
 └──────────────────────────────────────────────┘
      │  HTML  (+ Set-Cookie: admin_session=...)
      ▼
   브라우저에 출입 기록 열람실 화면 표시
```

**메타포 — 건물 출입 기록 열람실.** 회사 정문(Gateway)에는 모든 출입 시도를 한 줄씩 적어 두는 두꺼운 장부(`audit.jsonl`)가 있습니다. 누가(agent) 어느 방(server의 tool)에 들어가려 했고, 통과했는지 거부됐는지(decision)가 시각 순서대로 append만 됩니다 — 한 번 적은 줄은 고치지 않습니다. `admin.py`는 그 장부를 펼쳐 읽어 주는 **열람실 직원**입니다. 직원은 장부 원본을 절대 건드리지 않고(읽기 전용), 방문객이 들어올 때 사원증(token/쿠키)부터 확인합니다.

이 페이지가 데모의 클라이맥스인 이유는 코드 주석(admin.py 3~6행)에 명시돼 있습니다: 가장 설득력 있는 산출물은 "거부 장면 + audit log"이고, 이 admin이 그걸 사람이 보는 한 장의 리포트 — "support-agent가 ops 서버에 N번 접근을 시도했고 전부 거부됐다" — 로 바꿉니다. 즉 CCTV 모니터실처럼, 흩어진 기록을 모아 거부 사건만 빨갛게 강조해 한눈에 보여줍니다.

설계의 큰 줄기는 **단순함**입니다. 프론트엔드 프레임워크도, 데이터베이스도 없습니다. 매 요청마다 파일을 통째로 읽어 파이썬에서 집계·필터하고 jinja2로 HTML 문자열을 만들어 돌려줍니다. 데모 규모에서는 이게 충분히 빠르고, "append-only 파일이 곧 진실"이라 별도 인덱스를 유지할 이유가 없습니다.

| 파일 | 역할 |
|---|---|
| `admin.py` | `/admin` 라우트 등록, 토큰/쿠키 인증, audit JSONL 직독(`read_audit`), 거부 집계(`summarize_denials`), 상세 필터(`apply_filters`), 템플릿 렌더 |
| `admin.html` | jinja2 템플릿 — 상단 거부 요약표 + 필터 폼 + 하단 감사 로그 테이블. `decision=denied` 행을 빨갛게 강조 |

---

## 1. `admin.py` — 라우트·집계·인증·리더 (코드)

### 1-1. 모듈 헤더 — Jinja 환경과 XSS 방어

페이지가 생성될 때마다 새로 만들 필요가 없는 jinja2 환경은 모듈이 처음 로드될 때 딱 한 번만 구성합니다.

```python
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)
```

- **무엇:** `templates/` 폴더에서 `.html`을 찾는 jinja2 엔진을 만든다.
- **왜 `__file__.parent`:** 템플릿을 패키지 내부(`src/gateway/templates/`)에 두었기 때문. wheel 설치든, editable 설치든, 소스 직접 실행이든 — 어떤 방식으로 띄워도 "이 파이썬 파일 옆"을 기준으로 찾으므로 항상 발견됩니다(헤더 주석 16~18행). 현재 작업 폴더(cwd)에 의존하지 않는 게 핵심.
- **왜 `autoescape`:** audit 데이터에는 공격자가 통제할 수 있는 값(예: tool 인자 요약)이 섞일 수 있습니다. 자동 이스케이프를 켜 두면 `<script>` 같은 문자열이 HTML 태그가 아니라 그냥 글자로 출력돼 XSS(악성 스크립트 주입)를 막습니다.

### 1-2. `read_audit` — 장부 펼쳐 읽기 (리더 견고성)

```python
def read_audit(path: str) -> list[dict]:
    """audit JSONL을 dict 리스트로 읽는다. 미존재/빈 파일 → []. 손상된 줄 → skip + 경고."""
    p = Path(path)
    if not p.exists():
        return []  # 아직 호출이 한 건도 없으면 파일이 없다 — 에러가 아니라 빈 상태
    rows: list[dict] = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue  # 빈 줄 무시
        try:
            rows.append(json.loads(line))
        except ValueError:
            logger.warning("audit line %d unparseable, skipped (path=%s)", n, path)
    return rows
```

JSONL은 "한 줄에 JSON 객체 하나"인 파일 형식입니다. 장부의 한 줄 = 한 사건.

- **파일이 없으면 빈 리스트 반환(`[]`).** 이건 에러가 아닙니다. compose(도커 묶음)를 재기동하면 매번 깨끗한 상태로 시작하므로, 아직 tool 호출이 한 건도 없으면 파일 자체가 없는 게 **정상**입니다. 그래서 예외를 던지지 않고 "기록 없음" 상태로 넘어갑니다.
- **줄별로 try/except.** 한 줄이 깨졌다고(예: Gateway가 줄을 쓰다가 도중에 죽어 마지막 줄이 잘림) 페이지 전체가 죽으면 안 됩니다. 깨진 줄만 건너뛰고 경고 로그를 남긴 뒤 나머지는 정상 렌더 — 이게 스펙이 말하는 "부분 신뢰성"(eng review G2)입니다.
- **메타포:** 장부에 잉크가 번져 한 줄을 못 읽어도, 직원은 그 줄만 "판독 불가"로 넘기고 나머지 페이지를 계속 읽어 줍니다. 장부 전체를 덮어 버리지 않습니다.

### 1-3. 작은 도우미 두 개 — 서버 추출과 시각 파싱

```python
def _server_of(tool: str) -> str:
    """'ops__query_logs' -> 'ops'. audit엔 prefix 포함 tool 이름이 저장돼 있어 서버를 도로 추출."""
    return tool.split("__", 1)[0] if "__" in tool else tool
```

audit에는 `ops__query_logs`처럼 "어느 서버의 어느 tool인지"가 `서버__툴` 형태로 저장돼 있습니다. `__` 앞부분만 잘라내 서버 이름(`ops`)을 복원합니다. 거부 요약을 "agent × server" 단위로 묶으려면 이 서버 이름이 필요합니다.

```python
def _parse_ts(row: dict) -> datetime | None:
    """audit 행의 ts를 datetime으로. 누락/형식오류면 None(시간 필터에서 자연히 제외됨)."""
    try:
        return datetime.fromisoformat(row["ts"])
    except (KeyError, TypeError, ValueError):
        return None
```

ts(타임스탬프) 글자를 진짜 시각 객체로 바꿉니다. ts가 없거나 형식이 깨졌으면 `None`을 돌려주는데, 이게 영리한 점입니다 — 시간 범위 필터에서 `None`은 "시각을 모르는 행"이라 자연스럽게 탈락합니다. 별도의 에러 분기 없이 "모르면 제외"가 됩니다.

### 1-4. `summarize_denials` — 상단 거부 요약표 만들기

```python
def summarize_denials(rows: list[dict], now: datetime, hours: int = 24) -> Counter:
    cutoff = now - timedelta(hours=hours)
    counter: Counter = Counter()
    for r in rows:
        if r.get("decision") != "denied":
            continue  # 거부만 센다
        ts = _parse_ts(r)
        if ts is None or ts < cutoff:
            continue  # 시간 모르거나 윈도 밖이면 제외
        counter[(r.get("agent", "?"), _server_of(r.get("tool", "?")))] += 1
    return counter
```

- **무엇:** 지난 24시간(`hours=24` 기본값) 안에서 `decision == "denied"`인 행만 골라, `(agent, server)` 짝마다 몇 번 거부됐는지 셉니다.
- **`Counter`:** 파이썬의 자동 계수기. `counter[키] += 1`을 하면 그 키가 처음이어도 알아서 0부터 시작합니다. 즉 "support-agent가 ops에 접근 거부당한 횟수"를 한 줄로 누적.
- **왜 24h 고정:** 이 요약표는 페이지 상단의 "한눈에 보는 경보판"입니다. 사용자가 거는 필터와 무관하게 항상 "지난 24시간 거부 현황"을 보여줘야 일관된 기준점이 됩니다(아래 1-6에서 상세 테이블 필터와 분리됨).
- **메타포:** 모니터실 벽의 빨간 카운터 — "오늘 하루 어느 직원이 어느 출입 금지 구역에 몇 번 막혔나"를 한 장으로 요약.

### 1-5. `apply_filters` — 하단 상세 테이블용 거르개

```python
def apply_filters(rows, *, agent=None, decision=None, since_hours=None, now=None):
    out = rows
    if agent:
        out = [r for r in out if r.get("agent") == agent]
    if decision:
        out = [r for r in out if r.get("decision") == decision]
    if since_hours is not None and now is not None:
        cutoff = now - timedelta(hours=since_hours)
        out = [r for r in out if (ts := _parse_ts(r)) is not None and ts >= cutoff]
    return out
```

- **무엇:** 사용자가 폼에서 고른 조건(agent / decision / 최근 N시간)으로 상세 로그를 거릅니다. **지정 안 한 항목은 통과** — 셋 다 비어 있으면 원본 그대로.
- **왜 키워드 전용 인자(`*`):** 각 필터를 독립적으로 켜고 끄게 분리. `if agent:`, `if decision:`처럼 값이 있을 때만 좁히므로 필터를 자유롭게 조합할 수 있습니다.
- **`(ts := _parse_ts(r))` 바다코끼리 연산자:** ts를 파싱하면서 동시에 `None`이 아닌지 한 줄에서 검사. 시각을 모르는 행은 시간 필터에서 빠집니다.
- **요약표와의 차이:** `summarize_denials`는 항상 24h·거부만. `apply_filters`는 사용자가 자유롭게 조건을 거는 상세 뷰. 둘은 목적이 달라 의도적으로 분리돼 있습니다.

### 1-6. `register` — 라우트 등록·인증·렌더 (전부 연결되는 곳)

```python
def register(app: FastAPI, audit_path: str, admin_token: str | None) -> None:
```

- **무엇:** `/admin` 라우트를 FastAPI 앱에 붙입니다. `audit_path`(읽을 장부 위치)와 `admin_token`(env `ADMIN_TOKEN`)을 받아 **클로저로 가둡니다**.
- **왜 클로저:** 비밀(token)을 모듈 전역 변수로 두지 않고 핸들러 함수 안에 가두기 위해. env가 미설정이라 `admin_token=None`이면 **모든 접근을 403**으로 막습니다(인증 끌 수 없게 함).

#### 인증 도우미 두 개

```python
def _cookie_ok(request: Request) -> bool:
    if not admin_token:
        return False  # env 미설정이면 어떤 쿠키도 통과시키지 않는다
    cookie = request.cookies.get("admin_session")
    return bool(cookie) and hmac.compare_digest(cookie, admin_token)

def _token_ok(token: str | None) -> bool:
    return bool(token) and bool(admin_token) and hmac.compare_digest(token, admin_token)
```

- **`hmac.compare_digest` (상수 시간 비교):** 보통의 `==`는 글자가 처음 틀리는 순간 멈춰, 비교에 걸린 시간으로 "몇 글자까지 맞췄는지"를 공격자가 추측할 수 있습니다(타이밍 공격). 상수 시간 비교는 일치 여부와 무관하게 항상 같은 시간을 써 그 단서를 없앱니다. 사원증을 "끝까지 다 대조"하는 셈.
- **두 단계인 이유:** `_cookie_ok`은 이미 인증돼 쿠키를 가진 **재방문**, `_token_ok`은 `?token=`을 들고 온 **최초 진입**을 각각 확인합니다.

#### 라우트 핸들러 — 접근 제어 → 로드 → 렌더

```python
@app.get("/admin")
def admin(request: Request) -> Response:
    set_cookie = False
    if not _cookie_ok(request):
        if _token_ok(request.query_params.get("token")):
            set_cookie = True  # 토큰 유효 → 이번 응답에 쿠키를 심고 렌더(AC4)
        else:
            return HTMLResponse("forbidden", status_code=403)
```

접근 제어 흐름: **쿠키가 있으면 바로 통과 → 없으면 `?token=`으로 최초 1회 인증 → 둘 다 실패면 403.** 토큰이 유효하면 `set_cookie=True`로 표시해 두고, 이번 응답에 쿠키를 심습니다. 왜 이 구조냐면 — 쿼리 토큰은 브라우저 히스토리·서버 로그에 박제되므로, **딱 한 번만** 쓰고 이후엔 쿠키로 전환하는 게 안전합니다(주석 13~15행, eng review T2).

```python
        rows = read_audit(audit_path)
        now = datetime.now(timezone.utc)
        f_agent = request.query_params.get("agent") or None
        f_decision = request.query_params.get("decision") or None
        since_raw = request.query_params.get("since")
        since_hours = int(since_raw) if since_raw and since_raw.isdigit() else None
```

- 장부를 통째로 읽고(`rows`), 지금 시각을 UTC로 잡습니다.
- 쿼리 파라미터에서 필터 값을 꺼냅니다. 빈 문자열이면 `or None`으로 "필터 없음" 처리.
- **`since_raw.isdigit()`:** since는 숫자만 허용. 숫자가 아니면 `int()`가 터지거나 이상한 값이 들어올 수 있으니 미리 막습니다 — 입력 방어.

```python
        summary = summarize_denials(rows, now)
        filtered = apply_filters(rows, agent=f_agent, decision=f_decision, since_hours=since_hours, now=now)
        html = _env.get_template("admin.html").render(
            summary=sorted(summary.items()),
            total_denials=sum(summary.values()),
            rows=list(reversed(filtered)),  # 최신 호출이 위로 오도록 역순
            f_agent=f_agent or "",  # 폼이 현재 필터 상태를 유지하도록 되돌려 준다
            f_decision=f_decision or "",
            f_since=since_raw or "",
        )
        resp = HTMLResponse(html)
        if set_cookie:
            resp.set_cookie("admin_session", admin_token, httponly=True, samesite="strict")
        return resp
```

- **두 뷰를 만든다:** `summary`(항상 24h 거부 집계) + `filtered`(사용자 필터 적용 상세).
- **`reversed(filtered)`:** JSONL은 시간 순(오래된 것부터)으로 append됩니다. 화면에선 최신 사건이 맨 위에 와야 보기 좋으니 역순으로 뒤집습니다.
- **`f_agent`/`f_decision`/`f_since`를 되돌려 줌:** 폼이 방금 건 필터 값을 그대로 유지하도록(submit 후에도 입력칸이 비지 않게).
- **쿠키 속성:** `httponly`(JS가 못 읽음 → 탈취 완화), `samesite="strict"`(다른 사이트에서 온 요청엔 쿠키 안 붙음 → CSRF 완화). 데모 수준의 기본 방어.

---

## 2. `admin.html` — jinja2 템플릿 (렌더링 구조)

서버사이드 렌더링이라 자바스크립트 프레임워크가 없습니다. `<style>`은 한 블록에 박힌 최소 CSS뿐이고, 핵심은 jinja2 표현식(`{{ }}`, `{% %}`)이 파이썬에서 넘긴 데이터를 어떻게 표로 푸느냐입니다.

### 2-1. 거부 행 강조 CSS

```css
tr.denied { background: #fde8e8; }
.muted { color: #888; }
```

`denied` 클래스가 붙은 행은 연한 빨강 배경. **거부 사건이 한눈에 빨갛게 도드라지는 것**이 이 페이지의 시각적 핵심입니다. `muted`는 trace_id처럼 부차적인 정보를 회색으로 죽입니다.

### 2-2. 상단 거부 요약표

```jinja2
<h2>거부 요약 (지난 24h) — 총 {{ total_denials }}건</h2>
{% if summary %}
<table>
  <tr><th>agent</th><th>server</th><th>denied 횟수</th></tr>
  {% for (agent, server), count in summary %}
  <tr class="denied"><td>{{ agent }}</td><td>{{ server }}</td><td>{{ count }}</td></tr>
  {% endfor %}
</table>
{% else %}
<p class="muted">지난 24시간 거부 기록 없음.</p>
{% endif %}
```

- `{{ total_denials }}`로 총 거부 건수를 제목에 박습니다.
- `summary`(정렬된 `(agent, server) → count` 짝 목록)를 한 행씩 풉니다. 요약표는 본질이 거부 현황이라 **모든 행에 `denied` 클래스**를 붙여 빨갛게 칠합니다.
- **`{% if summary %} ... {% else %}`:** 거부가 하나도 없으면 빈 표 대신 "지난 24시간 거부 기록 없음." 문구. 빈 상태도 의미 있게 보여 주는 빈 상태 처리.

### 2-3. 필터 폼 (GET)

```jinja2
<form method="get" action="/admin">
  <input type="text" name="agent" placeholder="agent" value="{{ f_agent }}">
  <select name="decision">
    <option value="">모든 decision</option>
    {% for d in ["allowed", "denied", "auth_failed", "error"] %}
    <option value="{{ d }}"{% if f_decision == d %} selected{% endif %}>{{ d }}</option>
    {% endfor %}
  </select>
  <input type="number" name="since" placeholder="지난 N시간" value="{{ f_since }}">
  <button type="submit">필터</button>
</form>
```

- **`method="get"`:** 폼을 제출하면 조건이 URL 쿼리 파라미터(`?agent=...&decision=...&since=...`)가 됩니다. 그래서 `admin.py`가 `request.query_params`로 읽을 수 있고, 필터된 URL을 그대로 북마크·공유할 수 있습니다.
- **`value="{{ f_agent }}"` / `{% if f_decision == d %} selected{% endif %}`:** 방금 건 필터 값을 입력칸·드롭다운에 되돌려 넣어, 제출 후에도 폼이 현재 상태를 기억하게 합니다(1-6에서 되돌려 준 값들).
- decision 선택지는 `allowed / denied / auth_failed / error` 네 가지로 하드코딩 — Gateway가 남기는 decision 종류와 일치.

### 2-4. 하단 감사 로그 테이블

```jinja2
{% if rows %}
<table>
  <tr><th>ts</th><th>agent</th><th>tool</th><th>decision</th><th>args</th><th>trace_id</th></tr>
  {% for r in rows %}
  <tr{% if r.decision == "denied" %} class="denied"{% endif %}>
    <td>{{ r.ts }}</td>
    <td>{{ r.agent }}</td>
    <td><code>{{ r.tool }}</code></td>
    <td>{{ r.decision }}</td>
    <td>{{ r.args_summary }}</td>
    <td class="muted">{{ r.trace_id }}</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p class="muted">아직 기록 없음.</p>
{% endif %}
```

- 스펙이 요구한 6개 컬럼: **ts, agent, tool, decision, args_summary, trace_id.**
- **`{% if r.decision == "denied" %} class="denied"{% endif %}`:** 그 행이 거부면 빨강 강조. 요약표(항상 빨강)와 달리, 상세 테이블은 **거부 행만** 골라 도드라지게 합니다 — 통과·거부가 섞인 흐름에서 거부 사건이 즉시 눈에 띕니다.
- `r.trace_id`는 `muted`(회색)로 — 관측(observability)에서 한 요청을 추적하는 보조 ID라 시각적으로 가라앉힙니다.
- **`{% if rows %} ... {% else %}`:** 필터 결과가 없으면 "아직 기록 없음." — 빈 상태 처리.

---

## 3. Live wiring: admin이 audit JSONL을 어떻게 읽는지, S6 데모가 여기 어떻게 나타나는지

흐름을 끝까지 이어 보면:

1. **Gateway가 tool call을 처리할 때마다** 한 사건을 `audit.jsonl`에 한 줄씩 append합니다 (이 쓰기는 다른 모듈 담당이라 여기선 읽기만 다룸).
2. 누군가 `/admin?token=SECRET`을 엽니다. `register`가 가둔 `admin_token`(env `ADMIN_TOKEN`)과 대조해 통과시키고 쿠키를 심습니다.
3. `read_audit(audit_path)`가 그 시점의 `audit.jsonl`을 **통째로 다시 읽습니다.** DB도 캐시도 없으니, 페이지를 새로 고칠 때마다 항상 최신 파일 상태가 보입니다 — 파일이 곧 진실.
4. `summarize_denials`가 지난 24h 거부를 `(agent, server)`로 묶어 상단 경보판을 만들고, `apply_filters`가 사용자 조건으로 상세 테이블을 만듭니다.

**S6 데모(거부 시나리오)가 화면에 나타나는 모습:** demo-agent 중 예컨대 support-agent가 권한 없는 `ops__...` tool을 호출하면, Gateway가 YAML 정책으로 막고 `decision="denied"` 줄을 audit에 남깁니다. 그 줄이 `/admin`에서:
- **상단 요약표**에 `support-agent | ops | 3` 같은 빨간 행으로 집계되고,
- **하단 상세 테이블**에 해당 시각의 거부 행이 빨갛게 강조돼 — `tool`, `args`, `trace_id`까지 — 그대로 보입니다.

이렇게 "지난 24시간, 누가 민감 tool에 접근을 시도했고 전부 거부됐나"가 한 화면으로 증명됩니다. 그게 이 페이지가 데모의 클라이맥스인 이유입니다.

---

## 4. 관통하는 설계 원칙 요약

- **파일이 곧 진실(append-only).** 별도 DB·인덱스 없이 audit JSONL을 매 요청 직독한다. 데모 규모에선 전체 스캔이 충분히 빠르고, 단일 진실 원천이라 동기화 문제가 없다.
- **리더 견고성(부분 신뢰성).** 파일 미존재/빈 파일 → 빈 상태, 손상된 줄 → 그 줄만 skip + 경고. 한 줄이 깨졌다고 페이지 전체가 죽지 않는다(eng review G2).
- **빈 상태를 의미 있게.** compose 재기동마다 깨끗이 시작하므로 "기록 없음"이 기본 경로다. 요약표·상세표 모두 `{% if %}`로 빈 상태 문구를 정상 렌더한다.
- **데모 수준 인증, 명시적으로.** `?token=`은 최초 1회만(히스토리·로그 박제 회피), 이후 쿠키. 상수 시간 비교(`hmac.compare_digest`)·`httponly`·`samesite=strict`로 기본 방어. 진짜 세션/OIDC는 Out of Scope(eng review T2).
- **Simplicity First (Karpathy).** 프론트 프레임워크 없음, 서버사이드 렌더링 + 최소 CSS. 두 뷰(고정 24h 요약 / 사용자 필터 상세)를 목적별로 분리하되 그 이상 추상화하지 않는다.
- **기본은 안전(secure by default).** env `ADMIN_TOKEN` 미설정이면 `admin_token=None`이라 모든 접근을 403 — 인증을 끌 수 없다.
- **XSS 차단을 엔진 차원에서.** jinja2 `autoescape`로 공격자 통제 가능한 audit 값(args 요약 등)이 HTML로 주입되는 걸 기본 봉쇄한다.
- **거부를 시각적으로 강조.** `tr.denied` 빨강 배경으로, 통과·거부가 섞인 로그에서 거부 사건이 즉시 눈에 띈다 — 이 페이지의 존재 이유 그 자체.
