import pytest

from ticket_server import db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TICKET_DB_PATH", str(tmp_path / "tickets.db"))


def test_create_search_update_roundtrip():
    created = db.create_ticket("login broken", "users cannot log in")
    assert created["status"] == "open"

    hits = db.search_tickets("login")
    assert any(h["id"] == created["id"] for h in hits)

    updated = db.update_status(created["id"], "in_progress")
    assert updated == {"id": created["id"], "status": "in_progress"}


def test_update_status_invalid_value():
    created = db.create_ticket("t", "b")
    with pytest.raises(ValueError, match="invalid status"):
        db.update_status(created["id"], "done")


def test_update_status_missing_ticket():
    with pytest.raises(ValueError, match="not found"):
        db.update_status(999, "closed")
