"""마크다운 corpus 로딩 + BM25 검색."""

import os
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

_DEFAULT_CORPUS_DIR = Path(__file__).parents[2] / "corpus"

_corpus: dict[str, str] | None = None
_bm25: BM25Okapi | None = None
_doc_ids: list[str] = []


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9가-힣]+", text.lower())


def _load() -> None:
    global _corpus, _bm25, _doc_ids
    corpus_dir = Path(os.environ.get("DOCS_CORPUS_DIR", _DEFAULT_CORPUS_DIR))
    _corpus = {p.stem: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}
    _doc_ids = list(_corpus)
    _bm25 = BM25Okapi([_tokenize(_corpus[d]) for d in _doc_ids])


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


def read_doc(doc_id: str) -> dict:
    if _corpus is None:
        _load()
    if doc_id not in _corpus:
        raise ValueError(f"doc {doc_id!r} not found")
    return {"doc_id": doc_id, "content": _corpus[doc_id]}
