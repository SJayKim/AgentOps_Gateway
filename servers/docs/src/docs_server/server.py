"""docs-server — 읽기 전용 대표 백엔드 (:8102)."""

from mcp.server.fastmcp import FastMCP

from docs_server import search

mcp = FastMCP("docs-server", host="0.0.0.0", port=8102)


@mcp.tool()
def search_docs(query: str) -> list[dict]:
    """BM25 search over the markdown corpus. Returns top 5: doc_id, score, snippet."""
    return search.search_docs(query)


@mcp.tool()
def read_doc(doc_id: str) -> dict:
    """Read the full content of a document by doc_id."""
    return search.read_doc(doc_id)
