"""docs-server 실행 진입점 — `python -m docs_server`.

server.py의 FastMCP 인스턴스를 Streamable HTTP transport로 띄운다(Gateway가 붙는 transport와 일치).
"""

from docs_server.server import mcp

mcp.run(transport="streamable-http")
