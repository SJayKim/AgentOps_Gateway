"""SQLite 저장소 — 컨테이너/로컬 임시 DB. 볼륨 마운트 없음(재시작하면 데이터 사라짐).

[왜 SQLite, 왜 영속성 없음]
design.md 제약: 외부 의존성 없는 완전 통제 시뮬레이션 환경(Week 1~3). 별도 DB 서버를
띄우면 의존성·기동 복잡도가 늘어난다. SQLite 파일 하나면 충분하고, 데모는 매번 깨끗한
상태에서 시작하는 게 오히려 재현성에 좋아서 볼륨을 일부러 안 붙였다. ticket-server가
'쓰기 있는' 백엔드라 정책 enforcement(쓰기 권한 칸) 테스트의 표본이 된다.
"""

import os
import sqlite3
from datetime import datetime, timezone

# update_status가 받는 status를 화이트리스트로 고정 — 임의 문자열로 상태가 오염되는 걸 막는다.
VALID_STATUSES = {"open", "in_progress", "closed"}

# 멱등 스키마: IF NOT EXISTS라 매 연결마다 실행해도 안전(마이그레이션 도구 없이 자가 부트스트랩).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """DB 연결을 열고 스키마를 보장한다. 경로는 env로 바꿀 수 있어(테스트가 임시 파일 주입) 코드 수정 불필요.

    매 호출마다 새 연결을 여는 단순 모델 — 풀이 없다. MCP tool 호출 빈도가 낮은 데모라
    연결 비용이 문제되지 않고, 연결 수명 관리를 안 해도 돼 코드가 단순해진다.
    """
    conn = sqlite3.connect(os.environ.get("TICKET_DB_PATH", "tickets.db"))
    conn.execute(_SCHEMA)
    return conn


def create_ticket(title: str, body: str) -> dict:
    """티켓 생성. with 블록이 끝나며 커밋되고, lastrowid로 새 id를 돌려준다."""
    with _connect() as conn:  # sqlite3 connection의 context manager는 블록 정상 종료 시 commit
        cur = conn.execute(
            # 파라미터 바인딩(?)으로 SQL 인젝션 방지 — 사용자 입력(title/body)을 문자열에 직접 끼우지 않는다.
            "INSERT INTO tickets (title, body, status, created_at) VALUES (?, ?, 'open', ?)",
            (title, body, datetime.now(timezone.utc).isoformat()),
        )
        return {"id": cur.lastrowid, "status": "open"}


def search_tickets(query: str) -> list[dict]:
    """title 또는 body에 query가 substring으로 들어간 티켓을 반환(LIKE %query%)."""
    pattern = f"%{query}%"  # LIKE 와일드카드로 감싸 부분 일치 검색
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, status FROM tickets WHERE title LIKE ? OR body LIKE ?",
            (pattern, pattern),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "status": r[2]} for r in rows]


def update_status(ticket_id: int, status: str) -> dict:
    """티켓 상태 변경. 잘못된 status나 없는 ticket_id는 ValueError로 거절한다.

    여기서 던지는 ValueError는 FastMCP가 tool result의 isError로 변환해 에이전트에 전달한다 —
    '권한' 거부(Gateway 정책)와는 다른, 백엔드 '실행' 단계의 검증 실패다.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}: must be one of {sorted(VALID_STATUSES)}")
    with _connect() as conn:
        cur = conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        if cur.rowcount == 0:  # 매칭된 행이 0이면 그 id의 티켓이 없다는 뜻
            raise ValueError(f"ticket {ticket_id} not found")
    return {"id": ticket_id, "status": status}
