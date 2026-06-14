"""ticket-server 실행 진입점 — `python -m ticket_server`.

server.py에서 구성된 FastMCP 인스턴스를 Streamable HTTP transport로 띄운다. transport를
"streamable-http"로 고정한 이유: Gateway가 streamablehttp_client로 붙기 때문에 백엔드도
같은 transport여야 한다(design.md 스택: Streamable HTTP transport).
"""

from ticket_server.server import mcp

mcp.run(transport="streamable-http")
