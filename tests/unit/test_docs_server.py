import pytest

from docs_server import search


def test_search_docs_hits_seeded_word():
    hits = search.search_docs("incident")
    assert len(hits) >= 1
    assert all({"doc_id", "score", "snippet"} <= set(h) for h in hits)
    assert hits[0]["doc_id"] == "incident-runbook"


def test_read_doc_returns_full_content():
    result = search.read_doc("incident-runbook")
    assert result["doc_id"] == "incident-runbook"
    assert "Incident Runbook" in result["content"]


def test_read_doc_missing_raises():
    with pytest.raises(ValueError, match="not found"):
        search.read_doc("no-such-doc")
