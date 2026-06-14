"""ops-server — '민감 데이터' 역할 백엔드 (:8103).

[이 서버의 역할 — design.md 백엔드 매트릭스]
운영 메트릭·로그를 다루는, 권한 매트릭스에서 가장 민감한 백엔드. support-agent에겐
'전부 차단'되는 칸이라 S6 거부 데모의 무대가 된다. query_logs는 '인자 레벨 정책'(시간
범위 ≤24h, analyst-agent 한정)의 대상이기도 하다.

[중요 — 범위 제한은 여기 없다]
query_logs의 docstring이 "No range limit server-side"라고 못박은 건 의도다. 시간 범위
제한은 백엔드가 아니라 'Gateway 정책'(S4)이 강제한다. 통제 지점을 Gateway 한 곳에
모으는 게 이 프로젝트의 요지이므로, 백엔드는 순진하게 요청대로 데이터를 만들어 주고
"누가 무엇을 얼마나 볼 수 있나"의 판단은 전부 Gateway가 한다.
"""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from ops_server import fake_data

mcp = FastMCP("ops-server", host="0.0.0.0", port=8103)  # Gateway의 BACKEND_OPS_URL(:8103)과 짝


@mcp.custom_route("/health", methods=["GET"])  # docker compose healthcheck용(S5 P4)
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
