"""Gateway (:8000) — FastAPI + MCP Streamable HTTP.

요청 처리 경로는 call_tool 핸들러 하나로 유지 — S4가 "인증 → 정책 → 라우팅"
순서를 이 단일 경로에 끼워 넣었다 (미들웨어 추상화는 만들지 않는다).

인증 실패는 이중 계층 (eng review 이슈 1): tools/call은 MCP isError로
(S6 파싱 계약), 비-tool-call(initialize, tools/list)은 HTTP 401로 —
tool result는 tools/call 응답에만 존재하므로 transport 표준 의미론을 따른다.
"""

import contextlib
import json
import logging
import os
import time

import mcp.types as types
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from gateway import aggregate, audit, auth, observability, routes
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.upstream import Backend

logger = logging.getLogger(__name__)

# (prefix, env var, 로컬 기본 URL) — env 값은 MCP 엔드포인트 전체 URL
BACKEND_SPECS = [
    ("ticket", "BACKEND_TICKET_URL", "http://localhost:8101/mcp"),
    ("docs", "BACKEND_DOCS_URL", "http://localhost:8102/mcp"),
    ("ops", "BACKEND_OPS_URL", "http://localhost:8103/mcp"),
]


async def _buffer_body(receive):
    """receive를 소진해 body를 읽고, 동일 메시지를 재생하는 receive를 돌려준다."""
    messages = []
    body = b""
    while True:
        message = await receive()
        messages.append(message)
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    replay = iter(messages)

    async def replaying_receive():
        try:
            return next(replay)
        except StopIteration:
            return await receive()

    return body, replaying_receive


def _is_tools_call(body: bytes) -> bool:
    try:
        message = json.loads(body)
    except ValueError:
        return False
    return isinstance(message, dict) and message.get("method") == "tools/call"


def build_app() -> FastAPI:
    backends = {
        name: Backend(name, os.environ.get(env, default)) for name, env, default in BACKEND_SPECS
    }
    policy = Policy.load(os.environ.get("GATEWAY_POLICY_PATH", "policies/policy.yaml"))
    audit_path = os.environ.get("GATEWAY_AUDIT_PATH", "audit/audit.jsonl")

    server = Server("agentops-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await aggregate.aggregate_tools(backends)

    @server.call_tool(validate_input=False)  # 검증은 백엔드 몫 — 응답 그대로 중계
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        arguments = arguments or {}
        request = server.request_context.request  # transport가 붙인 starlette Request
        parts = aggregate.split(name)
        server_name, tool_name = parts if parts else ("unknown", name)
        tracer = observability.tracer()
        with tracer.start_as_current_span("tools/call") as span:
            trace_id = observability.trace_id_hex(span)
            duration_s = None
            try:
                with tracer.start_as_current_span("auth"):
                    agent = auth.authenticate(request.headers.get("authorization"))
            except auth.AuthError as e:
                agent, decision = "anonymous", "auth_failed"
                result = error_result("AUTH_FAILED", reason=e.reason)
            else:
                start = time.perf_counter()
                result, decision = await routes.route_call(backends, policy, agent, name, arguments)
                duration_s = time.perf_counter() - start
            observability.record_call(
                agent=agent,
                server=server_name,
                tool=tool_name,
                decision=decision,
                duration_s=duration_s,
            )
            audit.record(
                audit_path,
                agent=agent,
                tool=name,
                args=arguments,
                decision=decision,
                trace_id=trace_id,
            )
            logger.info(
                "tools/call %s agent=%s decision=%s trace_id=%s", name, agent, decision, trace_id
            )
            return result

    manager = StreamableHTTPSessionManager(app=server)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        known: dict[str, set[str]] = {}
        for backend in backends.values():
            try:
                await backend.ensure_session()
                known[backend.name] = {t.name for t in backend.tools or []}
            except Exception:
                # 기동 실패가 아니라 경고 — 지연 재집계가 이후 처리 (eng review T1)
                logger.warning("backend %s unreachable at startup, will retry lazily", backend.name)
        policy.warn_unknown_tools(known)  # YAML 오타 가시화 (eng review 이슈 5)
        async with manager.run():
            yield
        for backend in backends.values():
            await backend.close()

    # Mount가 아니라 정확 매칭 Route — Mount는 POST /mcp를 307 → /mcp/ 로
    # 리다이렉트해 Streamable HTTP 세션 플로우를 깨뜨린다 (FastMCP와 동일 방식)
    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            try:
                auth.authenticate(Headers(scope=scope).get("authorization"))
            except auth.AuthError as e:
                # tools/call만 통과 — 핸들러가 MCP isError로 응답·audit 기록
                if scope["method"] == "POST":
                    body, receive = await _buffer_body(receive)
                    if _is_tools_call(body):
                        await manager.handle_request(scope, receive, send)
                        return
                response = JSONResponse(
                    {"code": "AUTH_FAILED", "reason": e.reason}, status_code=401
                )
                await response(scope, receive, send)
                return
            await manager.handle_request(scope, receive, send)

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint=MCPEndpoint()))

    @app.get("/metrics")  # Prometheus 스크레이프 — 인증 없음 (내부 네트워크 전제)
    def metrics() -> Response:
        body, content_type = observability.metrics_payload()
        return Response(body, media_type=content_type)

    @app.get("/health")  # compose healthcheck용 경량 폴링
    def health() -> dict:
        return {"status": "ok"}

    return app
