"""admin 라우트 — 쿠키 인증·403·렌더·필터·빈/손상 파일 (AC4, AC5, AC5b).

백엔드 불필요 — /admin은 audit 파일만 읽는다. TestClient를 lifespan 없이 써서
(컨텍스트 매니저 미사용) 백엔드 연결 시도 없이 라우트만 검증한다.
"""

import json

from fastapi.testclient import TestClient

from gateway.app import build_app

TOKEN = "admin-token-0123456789"
DENIED = {
    "ts": "2026-06-14T11:00:00+00:00",
    "agent": "support-agent",
    "tool": "ops__query_logs",
    "args_summary": "{}",
    "decision": "denied",
    "trace_id": "t1",
}
ALLOWED = {
    "ts": "2026-06-14T11:01:00+00:00",
    "agent": "support-agent",
    "tool": "docs__search_docs",
    "args_summary": "{}",
    "decision": "allowed",
    "trace_id": "t2",
}


def _client(tmp_path, monkeypatch, rows, token=TOKEN):
    audit = tmp_path / "audit.jsonl"
    if rows is not None:
        audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("GATEWAY_AUDIT_PATH", str(audit))
    if token is None:
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ADMIN_TOKEN", token)
    return TestClient(build_app())


def test_no_cookie_no_token_is_403(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [DENIED])
    assert client.get("/admin").status_code == 403


def test_bad_token_is_403(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [DENIED])
    assert client.get("/admin", params={"token": "wrong"}).status_code == 403


def test_token_sets_cookie_then_cookie_only_works(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [DENIED, ALLOWED])
    first = client.get("/admin", params={"token": TOKEN})
    assert first.status_code == 200
    assert "admin_session" in first.cookies or "admin_session" in client.cookies
    assert "ops__query_logs" in first.text  # 거부 행 렌더
    assert "총 1건" in first.text  # 24h 요약 (DENIED 1건)
    # 쿠키만으로 (token 쿼리 없이) 재접근
    second = client.get("/admin")
    assert second.status_code == 200
    assert "ops__query_logs" in second.text


def test_decision_filter_excludes_allowed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [DENIED, ALLOWED])
    client.get("/admin", params={"token": TOKEN})  # 쿠키 확보
    resp = client.get("/admin", params={"decision": "denied"})
    assert resp.status_code == 200
    assert "ops__query_logs" in resp.text
    assert "docs__search_docs" not in resp.text  # allowed 행 미표시


def test_empty_file_renders_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [])
    resp = client.get("/admin", params={"token": TOKEN})
    assert resp.status_code == 200
    assert "아직 기록 없음" in resp.text


def test_admin_disabled_when_token_unset(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, [DENIED], token=None)
    assert client.get("/admin", params={"token": "anything"}).status_code == 403
