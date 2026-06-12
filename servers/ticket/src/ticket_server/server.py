"""ticket-server — 쓰기 있는 정책 테스트용 백엔드 (:8101)."""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ticket_server import db

mcp = FastMCP("ticket-server", host="0.0.0.0", port=8101)


@mcp.custom_route("/health", methods=["GET"])  # compose healthcheck용 (S5 P4)
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


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
