"""docs-server — '읽기 전용' 대표 백엔드 (:8102).

[이 서버의 역할 — design.md 백엔드 매트릭스]
모든 에이전트가 '읽기'는 허용되는 무해한 사내 문서 저장소 역할. 쓰기 tool이 없어 권한
매트릭스에서 "누구나 읽을 수 있는" 기준선이 된다(거부 장면은 주로 ops에서 나온다).
BM25 검색을 붙여 단순 grep이 아니라 '의미 있는 도구'처럼 보이게 했다.
"""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from docs_server import search

mcp = FastMCP("docs-server", host="0.0.0.0", port=8102)  # Gateway의 BACKEND_DOCS_URL(:8102)과 짝


@mcp.custom_route("/health", methods=["GET"])  # docker compose healthcheck용(S5 P4)
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@mcp.tool()
def search_docs(query: str) -> list[dict]:
    """BM25 search over the markdown corpus. Returns top 5: doc_id, score, snippet."""
    return search.search_docs(query)


@mcp.tool()
def read_doc(doc_id: str) -> dict:
    """Read the full content of a document by doc_id."""
    return search.read_doc(doc_id)
