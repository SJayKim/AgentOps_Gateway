"""ops-server — "민감 데이터" 역할 백엔드 (:8103)."""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ops_server import fake_data

mcp = FastMCP("ops-server", host="0.0.0.0", port=8103)


@mcp.custom_route("/health", methods=["GET"])  # compose healthcheck용 (S5 P4)
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@mcp.tool()
def get_metrics(metric: str) -> dict:
    """Get time-series points for a metric. Valid metrics: cpu, memory, requests."""
    return fake_data.get_metrics(metric)


@mcp.tool()
def query_logs(query: str, start: str, end: str) -> dict:
    """Search log lines between start and end (ISO8601). No range limit server-side."""
    return fake_data.query_logs(query, start, end)
