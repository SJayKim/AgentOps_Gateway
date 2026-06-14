"""ticket-server — '쓰기 있는' 정책 테스트용 백엔드 (:8101).

[이 서버의 역할 — design.md 백엔드 매트릭스]
3개 백엔드 중 '쓰기'(create/update)가 있는 유일한 서버다. 그래서 권한 매트릭스에서
"읽기는 되지만 쓰기는 안 되는" 칸(예: analyst-agent는 ticket 읽기만 허용)을 테스트할
표본이 된다. tool마다 별도 권한이 필요하다는 걸 보여주는 게 목적이라 의도적으로 쓰기
tool을 둔다.

[왜 FastMCP인가]
Gateway는 저수준 Server로 세밀히 제어하지만, 백엔드 3종은 단순 tool 노출이 전부라
보일러플레이트를 줄여주는 고수준 FastMCP를 쓴다. @mcp.tool() 데코레이터가 함수
시그니처·docstring에서 MCP tool 스키마를 자동 생성한다(아래 tool들의 타입 힌트와
설명 문자열이 곧 에이전트가 보는 tool 정의가 된다).
"""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ticket_server import db

# host 0.0.0.0 / port 8101 — 컨테이너 외부(Gateway)에서 접속 가능하게. Gateway의
# BACKEND_TICKET_URL 기본값(:8101/mcp)과 짝을 이룬다.
mcp = FastMCP("ticket-server", host="0.0.0.0", port=8101)


@mcp.custom_route(
    "/health", methods=["GET"]
)  # docker compose healthcheck용(S5 P4). MCP가 아닌 평문 GET
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# 아래 세 tool의 docstring은 단순 주석이 아니라 '에이전트가 읽는 tool 설명'이다 —
# 무엇을 받고 무엇을 돌려주는지, 유효 값은 무엇인지를 LLM이 정확히 알도록 명세한다.
@mcp.tool()
def create_ticket(title: str, body: str) -> dict:
    """Create a new ticket. Returns its id and initial status ("open")."""
    return db.create_ticket(title, body)


@mcp.tool()
def search_tickets(query: str) -> list[dict]:
    """Search tickets by title/body substring. Returns id, title, status per match."""
    return db.search_tickets(query)


@mcp.tool()
def update_status(ticket_id: int, status: str) -> dict:
    """Update a ticket's status. Valid statuses: open, in_progress, closed."""
    return db.update_status(ticket_id, status)
