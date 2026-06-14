"""마크다운 corpus 로딩 + BM25 검색.

[왜 BM25인가]
docs-server를 '그럴듯한 읽기 도구'로 보이게 하려면 단순 substring grep보다 랭킹이 있는
검색이 낫다. BM25는 외부 서비스·임베딩 모델 없이(=design.md의 '외부 의존성 없음' 제약을
지키며) 순수 파이썬으로 동작하는 고전 랭킹 함수라 이 데모에 딱 맞는다.

[모듈 전역 캐시 + lazy 로드]
corpus·인덱스는 모듈 전역에 한 번만 만들어 재사용한다. 첫 검색 때 lazy 로드해서 import만
으로 디스크를 읽지 않게 했다(테스트가 import 부수효과 없이 모듈을 들이게).
"""

import os
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

# 기본 corpus 위치: 이 파일 기준 servers/docs/corpus/. parents[2]는 .../docs_server에서
# 두 단계 위(src의 부모 = 패키지 루트)를 가리켜 corpus/를 찾는다. __file__ 기준이라 어느
# 작업 디렉터리에서 띄워도 깨지지 않는다.
_DEFAULT_CORPUS_DIR = Path(__file__).parents[2] / "corpus"

# 로드되기 전엔 None — 이 None이 "아직 안 읽음"의 신호로 lazy 로드를 트리거한다.
_corpus: dict[str, str] | None = None
_bm25: BM25Okapi | None = None
_doc_ids: list[str] = []


def _tokenize(text: str) -> list[str]:
    """소문자화 후 영문/숫자/한글 연속을 토큰으로. 구두점·공백은 버린다.

    한글(가-힣)을 토큰 클래스에 포함시킨 건 corpus에 한국어 문서가 섞일 수 있어서다 —
    기본 영어 토크나이저면 한글이 통째로 빠져 검색이 안 된다.
    """
    return re.findall(r"[a-z0-9가-힣]+", text.lower())


def _load() -> None:
    """corpus 디렉터리의 *.md를 전부 읽어 BM25 인덱스를 구축한다(전역 캐시 채움)."""
    global _corpus, _bm25, _doc_ids
    corpus_dir = Path(os.environ.get("DOCS_CORPUS_DIR", _DEFAULT_CORPUS_DIR))  # env로 교체 가능(테스트)
    # 파일명 stem(확장자 뺀 이름)을 doc_id로 사용. sorted로 순서를 고정해 BM25 인덱스가
    # 결정적이 되게 한다(같은 입력 → 같은 점수, 테스트 재현성).
    _corpus = {p.stem: p.read_text(encoding="utf-8") for p in sorted(corpus_dir.glob("*.md"))}
    _doc_ids = list(_corpus)
    # BM25Okapi는 '토큰화된 문서들'의 리스트로 인덱스를 만든다. _doc_ids 순서와 1:1 정렬되어
    # 점수 배열의 i번째가 _doc_ids[i] 문서에 대응한다.
    _bm25 = BM25Okapi([_tokenize(_corpus[d]) for d in _doc_ids])


def search_docs(query: str) -> list[dict]:
    """query에 대한 BM25 점수 상위 5개 문서를 반환(점수 0 이하는 제외)."""
    if _bm25 is None:
        _load()  # 첫 호출에서 인덱스 구축
    scores = _bm25.get_scores(_tokenize(query))  # 문서별 BM25 점수 배열(_doc_ids 순서)
    ranked = sorted(zip(_doc_ids, scores), key=lambda x: x[1], reverse=True)  # 점수 내림차순
    return [
        # snippet은 본문 앞 160자 미리보기 — 전체를 보내지 않아 응답을 가볍게(read_doc로 전체 조회).
        {"doc_id": doc_id, "score": float(score), "snippet": _corpus[doc_id][:160]}
        for doc_id, score in ranked[:5]
        if score > 0  # 매치가 전혀 없는 문서(점수 0)는 결과에서 뺀다
    ]


def read_doc(doc_id: str) -> dict:
    """doc_id로 문서 전문을 반환. 없는 id는 ValueError(FastMCP가 tool 오류로 변환)."""
    if _corpus is None:
        _load()
    if doc_id not in _corpus:
        raise ValueError(f"doc {doc_id!r} not found")
    return {"doc_id": doc_id, "content": _corpus[doc_id]}
