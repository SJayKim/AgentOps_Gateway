"""Gateway 통합 테스트 공용 — 백엔드 subprocess·gateway 기동·인증 클라이언트.

S3 test_gateway_core에서 추출, S4 매트릭스/인증 테스트가 공유한다.
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

PORTS = {"ticket": 8101, "docs": 8102, "ops": 8103}
MODULES = {"ticket": "ticket_server", "docs": "docs_server", "ops": "ops_server"}


def wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"port {port} not up")


def wait_port_free(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                time.sleep(0.2)
        except OSError:
            return
    raise TimeoutError(f"port {port} still up")


class BackendProc:
    def __init__(self, name: str, env: dict):
        self.name = name
        self.env = env
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", MODULES[self.name]],
            env={**os.environ, **self.env},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_port(PORTS[self.name])

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None
            wait_port_free(PORTS[self.name])


@asynccontextmanager
async def gateway():
    """Gateway를 테스트 이벤트 루프 안에서 uvicorn으로 기동."""
    # sse-starlette 전역 AppStatus 리셋 — 직전 테스트의 uvicorn 종료가 set한
    # should_exit가 남아 이후 모든 SSE 응답이 즉시 닫힌다 (프로세스당 uvicorn
    # 1개인 프로덕션에는 없는, 테스트 전용 이슈)
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


def token(agent: str) -> str:
    return issue_token(agent, os.environ["GATEWAY_JWT_SECRET"])


def auth_headers(agent: str) -> dict:
    return {"Authorization": f"Bearer {token(agent)}"}


@asynccontextmanager
async def mcp_client(url: str, agent: str = "dev-agent"):
    """인증된 MCP 클라이언트 — agent의 사전발급 토큰을 전 요청에 싣는다."""
    async with streamablehttp_client(url, headers=auth_headers(agent)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def payload(result):
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def err_payload(result):
    assert result.isError
    return json.loads(result.content[0].text)
