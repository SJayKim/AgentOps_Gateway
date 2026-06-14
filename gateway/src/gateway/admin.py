"""audit admin 페이지 — "지난 24시간, 누가 민감 tool 접근을 시도했나" 한 질문에 답한다.

[이 페이지가 데모의 클라이맥스]
design.md 전제 4: 거부 장면 + audit log가 가장 설득력 있는 산출물이다. 이 admin이
audit JSONL을 사람이 보는 거버넌스 리포트로 바꿔, "support-agent가 ops에 N번 접근을
시도했고 전부 거부됐다"를 한눈에 보여준다(S6 Part B).

[설계 결정]
- 데이터 소스는 audit JSONL '직독'. 별도 DB·인덱스를 두지 않는다 — append-only 파일이
  곧 진실이고, 데모 규모에선 매 요청 전체 스캔이 충분히 빠르다.
- 리더 견고성(eng review G2): 파일이 없거나 비어 있으면 빈 상태, 손상된 줄은 건너뛰고
  경고. 한 줄이 깨졌다고 페이지 전체가 죽으면 안 된다(부분 신뢰성).
- 접근 제어(eng review T2): 데모 수준이다. 최초 1회 ?token= 으로 env ADMIN_TOKEN을
  검증하고 쿠키를 심은 뒤, 이후엔 쿠키로 확인한다. 진짜 인증(세션·OIDC 등)은 Out of
  Scope이며 README에 데모 한정임을 명시한다.
- 템플릿을 패키지 내부(src/gateway/templates/)에 둔다 — wheel 설치/editable/소스 직접
  실행 어느 경로로 띄워도 __file__ 기준으로 항상 찾히기 때문(스펙의 gateway/templates/
  에서 의도적으로 옮김).
"""

import hmac
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

logger = logging.getLogger(__name__)

# Jinja 환경은 모듈 로드 시 1회 구성. autoescape로 audit 데이터(공격자 통제 가능한 인자
# 요약 등)가 HTML에 그대로 주입되는 XSS를 막는다.
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


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
            # 쓰기 도중 잘린 줄 등 손상 줄은 통째로 버리지 않고 그 줄만 건너뛴다(G2).
            logger.warning("audit line %d unparseable, skipped (path=%s)", n, path)
    return rows


def _server_of(tool: str) -> str:
    """'ops__query_logs' -> 'ops'. audit엔 prefix 포함 tool 이름이 저장돼 있어 서버를 도로 추출."""
    return tool.split("__", 1)[0] if "__" in tool else tool


def _parse_ts(row: dict) -> datetime | None:
    """audit 행의 ts를 datetime으로. 누락/형식오류면 None(시간 필터에서 자연히 제외됨)."""
    try:
        return datetime.fromisoformat(row["ts"])
    except (KeyError, TypeError, ValueError):
        return None


def summarize_denials(rows: list[dict], now: datetime, hours: int = 24) -> Counter:
    """지난 hours 시간 내 decision=denied 를 (agent, server) 단위로 집계한다.

    이 집계가 페이지 상단 요약표 — "지난 24h 누가 어느 서버에 접근을 거부당했나"를 만든다.
    """
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


def apply_filters(
    rows: list[dict],
    *,
    agent: str | None = None,
    decision: str | None = None,
    since_hours: int | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """하단 상세 테이블용 필터. agent / decision / 최근 N시간으로 거른다(미지정 항목은 통과).

    키워드 인자로 분리해 둬서 각 필터를 독립적으로 켜고 끌 수 있다. 셋 다 None이면 원본 그대로.
    """
    out = rows
    if agent:
        out = [r for r in out if r.get("agent") == agent]
    if decision:
        out = [r for r in out if r.get("decision") == decision]
    if since_hours is not None and now is not None:
        cutoff = now - timedelta(hours=since_hours)
        # 바다코끼리 연산자로 파싱과 None 검사를 한 줄에: ts 없는 행은 시간 필터에서 탈락.
        out = [r for r in out if (ts := _parse_ts(r)) is not None and ts >= cutoff]
    return out


def register(app: FastAPI, audit_path: str, admin_token: str | None) -> None:
    """/admin 라우트를 app에 등록한다. admin_token=None(env 미설정)이면 모든 접근을 403.

    토큰을 클로저로 가둬 라우트 핸들러가 공유하게 한다 — 모듈 전역에 비밀을 두지 않는다.
    """

    def _cookie_ok(request: Request) -> bool:
        """이미 인증돼 쿠키를 가진 재방문인지 검사."""
        if not admin_token:
            return False  # env 미설정이면 어떤 쿠키도 통과시키지 않는다
        cookie = request.cookies.get("admin_session")
        # 상수 시간 비교(hmac.compare_digest)로 타이밍 공격 여지를 없앤다.
        return bool(cookie) and hmac.compare_digest(cookie, admin_token)

    def _token_ok(token: str | None) -> bool:
        """최초 진입의 ?token= 값이 ADMIN_TOKEN과 일치하는지 검사(역시 상수 시간 비교)."""
        return bool(token) and bool(admin_token) and hmac.compare_digest(token, admin_token)

    @app.get("/admin")
    def admin(request: Request) -> Response:
        # --- 접근 제어: 쿠키가 있으면 통과, 없으면 ?token= 으로 최초 1회 인증 ---
        set_cookie = False
        if not _cookie_ok(request):
            if _token_ok(request.query_params.get("token")):
                set_cookie = True  # 토큰 유효 → 이번 응답에 쿠키를 심고 렌더(AC4)
            else:
                return HTMLResponse("forbidden", status_code=403)

        # --- 데이터 로드 및 쿼리 파라미터 파싱 ---
        rows = read_audit(audit_path)
        now = datetime.now(timezone.utc)
        f_agent = request.query_params.get("agent") or None
        f_decision = request.query_params.get("decision") or None
        since_raw = request.query_params.get("since")
        # since는 숫자만 허용 — isdigit로 막아 int() 예외/주입을 차단한다.
        since_hours = int(since_raw) if since_raw and since_raw.isdigit() else None

        # --- 요약(거부 집계, 항상 24h 고정) + 상세(사용자 필터 적용) 두 뷰를 만든다 ---
        summary = summarize_denials(rows, now)
        filtered = apply_filters(
            rows, agent=f_agent, decision=f_decision, since_hours=since_hours, now=now
        )
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
            # httponly: JS가 못 읽게(쿠키 탈취 완화). samesite=strict: CSRF 완화. 데모 수준의 기본 방어.
            resp.set_cookie("admin_session", admin_token, httponly=True, samesite="strict")
        return resp
