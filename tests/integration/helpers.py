"""Gateway 통합 테스트 공용 도구 — 백엔드 subprocess 제어·gateway 기동·인증 클라이언트.

S3 test_gateway_core에서 추출했고, 이후 S4 매트릭스/인증 테스트가 공유한다. 여기 모인
함수들은 "테스트가 진짜 네트워크 위에서 Gateway를 돌리려면 필요한 잔배관"이다 — 포트가
열릴 때까지 기다리기, 빈 포트 받기, uvicorn을 테스트 루프 안에서 띄우기 등.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from issue_tokens import issue_token
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gateway.app import build_app

PORTS = {"ticket": 8101, "docs": 8102, "ops": 8103}  # 백엔드별 고정 포트(서버 코드의 바인딩과 일치)
MODULES = {"ticket": "ticket_server", "docs": "docs_server", "ops": "ops_server"}


def wait_port(port: int, timeout: float = 15.0) -> None:
    """포트가 '열릴' 때까지 폴링한다. subprocess 기동은 비동기라 즉시 접속되지 않기 때문.

    Popen이 돌아왔다고 서버가 listen 중인 건 아니다 — 실제 connect가 성공할 때까지 기다려야
    바로 뒤따르는 요청이 connection refused로 깨지지 않는다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return  # 접속 성공 = 서버가 떴다
        except OSError:
            time.sleep(0.2)  # 아직 안 뜸 — 잠깐 쉬고 재시도
    raise TimeoutError(f"port {port} not up")


def wait_port_free(port: int, timeout: float = 15.0) -> None:
    """포트가 '비워질' 때까지 폴링한다(wait_port의 반대). 종료 직후 재기동의 포트 충돌 방지.

    terminate 후에도 OS가 소켓을 즉시 회수하지 않을 수 있다. 다음 테스트가 같은 포트로
    재기동할 때 'address already in use'로 깨지지 않도록 정말 닫힐 때까지 확인한다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                time.sleep(0.2)  # 아직 누가 듣고 있음 — 더 기다린다
        except OSError:
            return  # 접속 실패 = 포트가 비었다
    raise TimeoutError(f"port {port} still up")


class BackendProc:
    """백엔드 MCP 서버 하나를 별도 프로세스로 띄우고 죽이는 핸들. 죽임/재기동 테스트가 이걸 쓴다."""

    def __init__(self, name: str, env: dict):
        self.name = name
        self.env = env  # 이 백엔드에만 줄 추가 env(예: ticket의 TICKET_DB_PATH)
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        # `python -m <module>`로 서버를 띄운다. stdout/stderr는 버려 테스트 출력을 깨끗하게 유지.
        self.proc = subprocess.Popen(
            [sys.executable, "-m", MODULES[self.name]],
            env={**os.environ, **self.env},  # 부모 env에 백엔드별 env를 덮어쓴 형태로 전달
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_port(PORTS[self.name])  # 포트가 실제로 열릴 때까지 블록 — 기동 완료 보장

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()  # SIGTERM으로 정상 종료 요청
            self.proc.wait(timeout=10)  # 좀비 방지 — 완전히 끝날 때까지 수거
            self.proc = None
            wait_port_free(PORTS[self.name])  # 포트가 비워져야 재기동이 안전


@asynccontextmanager
async def gateway():
    """Gateway를 '테스트의 이벤트 루프 안에서' uvicorn으로 기동하고 그 URL을 내준다.

    [왜 별 프로세스가 아니라 같은 루프인가]
    백엔드와 달리 Gateway는 테스트 코드와 같은 프로세스/루프에서 띄운다. 그래야 build_app()을
    테스트가 주입한 env(정책·audit 경로)로 직접 만들고, 같은 루프의 MCP 클라이언트로 곧장
    호출할 수 있다.
    """
    # [테스트 전용 함정] sse-starlette는 should_exit를 '전역(AppStatus)'에 둔다. 직전 테스트의
    # uvicorn이 종료하며 should_exit=True를 남기면, 다음 테스트의 SSE 응답이 즉시 닫혀 버린다.
    # 프로덕션은 프로세스당 uvicorn 1개라 안 생기지만, 한 프로세스에서 gateway를 여러 번 띄우는
    # 테스트에선 매번 이 전역을 직접 리셋해 줘야 한다.
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    # 포트 0으로 bind해 OS가 비어있는 포트를 골라 주게 한다 — 하드코딩 포트의 충돌을 피한다.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())  # 서버를 백그라운드 task로 가동
    # 기동 완료를 폴링으로 기다린다. task.done()이면 serve가 예외로 죽은 것 → result()로 그
    # 예외를 다시 띄워 테스트가 조용히 매달리지 않게 한다.
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True  # 종료 신호
        await task  # serve task가 깨끗이 끝날 때까지 대기


def token(agent: str) -> str:
    """테스트용 토큰 — 프로덕션 발급 함수(issue_token)를 그대로 써서 진짜 토큰과 동일하게 만든다."""
    return issue_token(agent, os.environ["GATEWAY_JWT_SECRET"])


def auth_headers(agent: str) -> dict:
    """agent의 Bearer 토큰을 담은 Authorization 헤더 dict."""
    return {"Authorization": f"Bearer {token(agent)}"}


@asynccontextmanager
async def mcp_client(url: str, agent: str = "dev-agent"):
    """인증된 MCP 클라이언트 — 지정 agent의 사전발급 토큰을 전 요청 헤더에 싣는다.

    기본 agent를 dev-agent로 둔 건 그가 권한 매트릭스에서 가장 넓은 권한(읽기+쓰기 전부)을
    가져, 인증/권한이 아니라 '기능'을 보려는 테스트가 권한 거부에 걸리지 않게 하기 위해서다.
    """
    async with streamablehttp_client(url, headers=auth_headers(agent)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def payload(result):
    """성공 tool result에서 실제 데이터를 꺼낸다.

    MCP는 구조화 결과를 structuredContent로 줄 수도, 텍스트 블록의 JSON으로 줄 수도 있다 —
    둘 다 대응해 테스트가 transport 표현 차이에 흔들리지 않게 한다.
    """
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def err_payload(result):
    """오류 result에서 구조화 payload({"code", ...})를 꺼낸다. isError가 맞는지부터 단언한다."""
    assert result.isError
    return json.loads(result.content[0].text)
