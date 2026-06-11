"""Gateway (:8000) — FastAPI + MCP Streamable HTTP.

요청 처리 경로는 call_tool 핸들러 하나로 유지 — S4가 "인증 → 정책 → 라우팅"
순서를 이 단일 경로에 끼워 넣는다 (미들웨어 추상화는 만들지 않는다).
"""

import contextlib
import logging
import os

import mcp.types as types
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.routing import Route

from gateway import aggregate, routes
from gateway.upstream import Backend

logger = logging.getLogger(__name__)

# (prefix, env var, 로컬 기본 URL) — env 값은 MCP 엔드포인트 전체 URL
BACKEND_SPECS = [
    ("ticket", "BACKEND_TICKET_URL", "http://localhost:8101/mcp"),
    ("docs", "BACKEND_DOCS_URL", "http://localhost:8102/mcp"),
    ("ops", "BACKEND_OPS_URL", "http://localhost:8103/mcp"),
]


def build_app() -> FastAPI:
    backends = {
        name: Backend(name, os.environ.get(env, default)) for name, env, default in BACKEND_SPECS
    }

    server = Server("agentops-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await aggregate.aggregate_tools(backends)

    @server.call_tool(validate_input=False)  # 검증은 백엔드 몫 — 응답 그대로 중계
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await routes.route_call(backends, name, arguments or {})

    manager = StreamableHTTPSessionManager(app=server)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        for backend in backends.values():
            try:
                await backend.ensure_session()
            except Exception:
                # 기동 실패가 아니라 경고 — 지연 재집계가 이후 처리 (eng review T1)
                logger.warning("backend %s unreachable at startup, will retry lazily", backend.name)
        async with manager.run():
            yield
        for backend in backends.values():
            await backend.close()

    # Mount가 아니라 정확 매칭 Route — Mount는 POST /mcp를 307 → /mcp/ 로
    # 리다이렉트해 Streamable HTTP 세션 플로우를 깨뜨린다 (FastMCP와 동일 방식)
    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint=MCPEndpoint()))
    return app
