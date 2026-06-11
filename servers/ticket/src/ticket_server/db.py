"""SQLite 저장소 — 컨테이너/로컬 임시, 볼륨 마운트 없음."""

import os
import sqlite3
from datetime import datetime, timezone

VALID_STATUSES = {"open", "in_progress", "closed"}

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
    conn = sqlite3.connect(os.environ.get("TICKET_DB_PATH", "tickets.db"))
    conn.execute(_SCHEMA)
    return conn


def create_ticket(title: str, body: str) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (title, body, status, created_at) VALUES (?, ?, 'open', ?)",
            (title, body, datetime.now(timezone.utc).isoformat()),
        )
        return {"id": cur.lastrowid, "status": "open"}


def search_tickets(query: str) -> list[dict]:
    pattern = f"%{query}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, status FROM tickets WHERE title LIKE ? OR body LIKE ?",
            (pattern, pattern),
        ).fetchall()
    return [{"id": r[0], "title": r[1], "status": r[2]} for r in rows]


def update_status(ticket_id: int, status: str) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}: must be one of {sorted(VALID_STATUSES)}")
    with _connect() as conn:
        cur = conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        if cur.rowcount == 0:
            raise ValueError(f"ticket {ticket_id} not found")
    return {"id": ticket_id, "status": status}
