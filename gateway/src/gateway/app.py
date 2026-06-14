"""Gateway (:8000) — FastAPI + MCP Streamable HTTP.

[이 모듈의 역할]
프로젝트 전체의 단일 진입점(Gateway)을 조립하는 곳. 에이전트(클라이언트)는 백엔드 MCP
서버에 직접 붙지 않고 항상 이 Gateway를 거친다. 그 한가운데에서 "인증 → 정책 → 라우팅
→ 관측·감사"를 한 경로로 강제하는 것이 존재 이유다(design.md: 직접 연결 구조에선 tool
call 단위 통제가 불가능).

[설계 결정 — 왜 이렇게 만들었나]
- 요청 처리 경로는 call_tool 핸들러 하나로 유지한다. S4가 "인증 → 정책 → 라우팅" 순서를
  이 단일 함수 안에 직접 끼워 넣었다. 미들웨어 체인 같은 추상화는 일부러 만들지 않았다 —
  경로가 하나뿐이라 추상화의 이득보다 추적 난이도만 늘기 때문(CLAUDE.md "Simplicity First").
- 인증 실패 응답은 이중 계층이다 (eng review 이슈 1):
    * tools/call 요청            → MCP tool result의 isError=true 로 거부(S6 파싱 계약)
    * 그 외(initialize, tools/list) → HTTP 401
  tool result는 tools/call 응답에만 존재하므로, 그게 없는 요청은 transport 표준(401)을 따른다.
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

from gateway import admin, aggregate, audit, auth, observability, routes
from gateway.errors import error_result
from gateway.policy import Policy
from gateway.upstream import Backend

logger = logging.getLogger(__name__)

# 등록된 백엔드 명세 — (prefix, env var 이름, 로컬 기본 URL). prefix는 tool 네임스페이싱과
# 라우팅 키를 겸한다. URL을 env로 주입 가능하게 둬서 같은 코드가 로컬(localhost)과
# compose(서비스명 DNS) 양쪽에서 돈다 — Dockerfile/compose가 env로 덮어쓴다.
BACKEND_SPECS = [
    ("ticket", "BACKEND_TICKET_URL", "http://localhost:8101/mcp"),
    ("docs", "BACKEND_DOCS_URL", "http://localhost:8102/mcp"),
    ("ops", "BACKEND_OPS_URL", "http://localhost:8103/mcp"),
]


async def _buffer_body(receive):
    """ASGI receive 채널을 소진해 body 전체를 읽고, 똑같이 재생하는 receive를 돌려준다.

    [왜 필요한가]
    ASGI body는 receive()로 청크 단위로 '한 번만' 흘려보낼 수 있다. 인증 실패 시점에
    "이게 tools/call인가?"를 보려고 body를 미리 읽어야 하는데, 그냥 읽으면 정작 MCP
    매니저에 넘길 body가 사라진다. 그래서 읽은 메시지를 버퍼에 모아 두고 처음부터 다시
    흘려주는 receive 대체물을 만들어 준다.
    """
    messages = []
    body = b""
    while True:
        message = await receive()
        messages.append(message)  # 원본 메시지를 그대로 보관(재생용)
        body += message.get("body", b"")
        if not message.get("more_body"):  # more_body=False면 마지막 청크
            break
    replay = iter(messages)

    async def replaying_receive():
        # 버퍼에 남은 메시지를 먼저 토해내고, 다 떨어지면 원래 receive로 위임한다.
        try:
            return next(replay)
        except StopIteration:
            return await receive()

    return body, replaying_receive


def _is_tools_call(body: bytes) -> bool:
    """JSON-RPC body가 method == "tools/call"인지 판정. 파싱 실패는 안전하게 False."""
    try:
        message = json.loads(body)
    except ValueError:
        return False  # JSON이 아니면 tools/call일 수 없다
    return isinstance(message, dict) and message.get("method") == "tools/call"


def build_app() -> FastAPI:
    """Gateway FastAPI 앱을 조립해 반환하는 팩토리.

    [왜 팩토리 함수인가]
    전역으로 앱을 만들지 않고 함수로 감쌌다. 테스트가 build_app()을 여러 번 호출해 매번
    깨끗한 앱(백엔드/정책/감사 경로가 env로 주입된)을 얻을 수 있어야 하기 때문이다.
    환경 의존은 전부 이 함수 진입 시점에 os.environ에서 한 번 읽는다.
    """
    # 백엔드 prefix → Backend 객체("백엔드당 MCP 세션 1개"를 들고 있음 — upstream.py 참조).
    backends = {
        name: Backend(name, os.environ.get(env, default)) for name, env, default in BACKEND_SPECS
    }
    # 정책은 기동 시 1회 로드(핫 리로드 없음). default-deny 엔진.
    policy = Policy.load(os.environ.get("GATEWAY_POLICY_PATH", "policies/policy.yaml"))
    # 감사 로그 경로만 보관하고 기록 시점에 연다(append-only JSONL).
    audit_path = os.environ.get("GATEWAY_AUDIT_PATH", "audit/audit.jsonl")

    # MCP 저수준 Server — tools/list, tools/call 두 핸들러만 등록한다. Gateway는 자체 tool을
    # 갖지 않고 백엔드 tool을 prefix로 묶어 중계하는 게 전부다.
    server = Server("agentops-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        # 전 백엔드 tool을 prefix 붙여 집계. 정책으로 필터링하지 않는다 — 의도된 설계
        # (support-agent도 ops tool의 '존재'는 봐야 S6 거부 데모가 성립). aggregate.py 참조.
        return await aggregate.aggregate_tools(backends)

    @server.call_tool(
        validate_input=False
    )  # 인자 스키마 검증은 백엔드 몫 — Gateway는 응답을 그대로 중계
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        """모든 tool 호출이 지나는 단일 처리 경로: 인증 → 정책·라우팅 → 관측 → 감사.

        반환은 항상 CallToolResult 하나 — 성공/거부/오류 모두 MCP 표준 result로 표현된다
        (거부도 예외가 아니라 isError result. design.md "거부 응답 형식" 계약).
        """
        arguments = arguments or {}  # 인자 없는 호출을 None으로 줄 수 있어 빈 dict로 정규화
        request = server.request_context.request  # transport가 span에 붙여 둔 starlette Request
        parts = aggregate.split(name)  # "ticket__create_ticket" → ("ticket", "create_ticket")
        # 관측/감사 라벨용 분해. 미상이면 ("unknown", 원본 이름)으로 라벨만 채운다(실제
        # UNKNOWN_TOOL 판정은 routes.route_call이 다시 한다).
        server_name, tool_name = parts if parts else ("unknown", name)
        tracer = observability.tracer()
        # tools/call 한 건 전체를 감싸는 최상위 trace span. 자식 span(auth/policy/backend_call)이
        # 한 trace_id를 공유하고, 그 id가 audit·로그에 함께 박힌다(교차 추적).
        with tracer.start_as_current_span("tools/call") as span:
            trace_id = observability.trace_id_hex(span)
            duration_s = None  # 인증 실패 시 백엔드까지 못 가므로 latency는 None으로 남긴다
            try:
                # 1) 인증 — Authorization 헤더의 JWT를 검증해 agent_id를 얻는다.
                with tracer.start_as_current_span("auth"):
                    agent = auth.authenticate(request.headers.get("authorization"))
            except auth.AuthError as e:
                # 인증 실패: 정책·라우팅을 건너뛰고 곧장 AUTH_FAILED result. agent는 "anonymous"로
                # 라벨링해 "인증 실패가 있었다"는 사실을 메트릭·감사에 남긴다.
                agent, decision = "anonymous", "auth_failed"
                result = error_result("AUTH_FAILED", reason=e.reason)
            else:
                # 2) 인증 통과 → 정책 평가 + 백엔드 중계. latency는 여기서만 측정한다.
                start = time.perf_counter()
                result, decision = await routes.route_call(backends, policy, agent, name, arguments)
                duration_s = time.perf_counter() - start
            # 3) 관측: 메트릭 기록(decision별 카운트, 정책 거부 카운트, latency 히스토그램).
            observability.record_call(
                agent=agent,
                server=server_name,
                tool=tool_name,
                decision=decision,
                duration_s=duration_s,
            )
            # 4) 감사: append-only JSONL에 한 줄. 메트릭과 '같은 곳에서, 같은 decision'으로
            #    기록해 두 시스템이 같은 사실을 보게 한다(eng review 이슈 2).
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

    # Streamable HTTP transport 매니저 — 위 Server를 HTTP 세션 프로토콜로 노출한다.
    manager = StreamableHTTPSessionManager(app=server)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """앱 수명 동안의 자원 관리: 기동 시 백엔드 세션 워밍업, 종료 시 정리.

        [왜 기동 때 미리 붙나]
        첫 tools/list까지 미루지 않고 기동 때 한 번 붙어 tool 목록을 캐시한다. 그래야 (1)
        첫 요청 latency가 튀지 않고, (2) 정책 YAML의 tool 오타를 기동 시점에 바로 경고할 수
        있다(warn_unknown_tools는 집계된 tool 목록이 있어야 동작).
        """
        known: dict[str, set[str]] = {}  # 정책 검증용 "실제 존재하는" tool 목록
        for backend in backends.values():
            try:
                await backend.ensure_session()
                known[backend.name] = {t.name for t in backend.tools or []}
            except Exception:
                # 백엔드 하나가 기동 때 죽어 있어도 Gateway는 뜬다 — 치명 실패가 아니라 경고.
                # 이후 요청 때 지연 재집계로 다시 시도된다(eng review T1: 부분 가용성 > 전체 다운).
                logger.warning("backend %s unreachable at startup, will retry lazily", backend.name)
        policy.warn_unknown_tools(
            known
        )  # YAML 오타 가시화(default-deny에선 오타가 조용한 거부 — 이슈 5)
        async with manager.run():  # transport 세션 매니저 가동
            yield  # ← 여기서 앱이 요청을 처리하는 동안 머문다
        # 종료: 백엔드 세션을 소유 task에서 깨끗이 닫는다(anyio cancel scope 문제 회피).
        for backend in backends.values():
            await backend.close()

    # /mcp는 Mount가 아니라 '정확 경로 매칭' Route로 단다.
    # [왜] Starlette Mount는 POST /mcp 를 307로 /mcp/ 로 리다이렉트하는데, 그 리다이렉트가
    # Streamable HTTP의 세션 핸드셰이크 흐름을 깨뜨린다. FastMCP도 같은 이유로 정확 매칭을 쓴다.
    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            # transport 매니저에 넘기기 전에 인증을 먼저 본다(위 이중 계층의 바깥 껍데기).
            try:
                auth.authenticate(Headers(scope=scope).get("authorization"))
            except auth.AuthError as e:
                # 인증 실패라도 tools/call이면 통과시킨다 — 안쪽 call_tool 핸들러가 MCP
                # isError(AUTH_FAILED)로 응답하고 audit까지 남기게 하기 위해서다(401로 끊으면
                # 거부 사실이 audit/메트릭에 안 남는다).
                if scope["method"] == "POST":
                    body, receive = await _buffer_body(receive)  # body를 읽되 재생 가능하게
                    if _is_tools_call(body):
                        await manager.handle_request(scope, receive, send)
                        return
                # tools/call이 아닌(initialize, tools/list 등) 인증 실패 → transport 표준대로 HTTP 401.
                response = JSONResponse(
                    {"code": "AUTH_FAILED", "reason": e.reason}, status_code=401
                )
                await response(scope, receive, send)
                return
            # 인증 통과 → 정상 MCP 처리.
            await manager.handle_request(scope, receive, send)

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(Route("/mcp", endpoint=MCPEndpoint()))

    @app.get("/metrics")  # Prometheus 스크레이프 — 인증 없음(내부 네트워크 전제)
    def metrics() -> Response:
        body, content_type = observability.metrics_payload()
        return Response(body, media_type=content_type)

    @app.get("/health")  # docker compose healthcheck가 폴링하는 경량 엔드포인트
    def health() -> dict:
        return {"status": "ok"}

    # /admin 거버넌스 페이지 등록(S6 Part B). ADMIN_TOKEN 미설정이면 admin 내부에서 전부 403.
    admin.register(app, audit_path, os.environ.get("ADMIN_TOKEN"))
    return app
