"""Gateway 코어 AC1-7 — 실제 백엔드 subprocess + 실제 Streamable HTTP 라운드트립.

백엔드 3종은 모듈 단위 subprocess로 기동 (도커 불필요 — CI에서 그대로 돈다).
AC6/AC7은 subprocess kill/재기동으로 장애 시나리오를 실제로 재현한다.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gateway.app import build_app

EXPECTED_PREFIXED = {
    "ticket__create_ticket",
    "ticket__search_tickets",
    "ticket__update_status",
    "docs__search_docs",
    "docs__read_doc",
    "ops__get_metrics",
    "ops__query_logs",
}

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


@pytest.fixture(scope="module")
def backends(tmp_path_factory):
    db = tmp_path_factory.mktemp("gw") / "tickets.db"
    procs = {
        "ticket": BackendProc("ticket", {"TICKET_DB_PATH": str(db)}),
        "docs": BackendProc("docs", {}),
        "ops": BackendProc("ops", {}),
    }
    for p in procs.values():
        p.start()
    yield procs
    for p in procs.values():
        p.stop()


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


@asynccontextmanager
async def mcp_client(url: str):
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def payload(result):
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def test_ac1_tools_list_exactly_seven_prefixed(backends):
    async with gateway() as url, mcp_client(url) as session:
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_PREFIXED


async def test_ac2_create_ticket_matches_direct_backend(backends):
    async with gateway() as url, mcp_client(url) as session:
        via_gw = await session.call_tool(
            "ticket__create_ticket", {"title": "via gateway", "body": "b"}
        )
        assert not via_gw.isError
    async with mcp_client("http://localhost:8101/mcp") as direct_session:
        direct = await direct_session.call_tool("create_ticket", {"title": "direct", "body": "b"})
        assert not direct.isError
    gw_p, d_p = payload(via_gw), payload(direct)
    assert set(gw_p) == set(d_p)  # 동일한 응답 구조
    assert gw_p["status"] == d_p["status"] == "open"


async def test_ac3_one_tool_per_backend(backends):
    async with gateway() as url, mcp_client(url) as session:
        t = await session.call_tool("ticket__search_tickets", {"query": "x"})
        d = await session.call_tool("docs__search_docs", {"query": "deployment"})
        o = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not t.isError and not d.isError and not o.isError


async def test_ac4_unknown_tool_variants(backends):
    async with gateway() as url, mcp_client(url) as session:
        for bad in ("unknown__x", "create_ticket", "ticket__nonexistent"):
            result = await session.call_tool(bad, {})
            assert result.isError, bad
            assert json.loads(result.content[0].text) == {"code": "UNKNOWN_TOOL", "tool": bad}


async def test_ac5_ten_concurrent_calls_shared_session(backends):
    async with gateway() as url, mcp_client(url) as session:

        async def call(i: int):
            result = await session.call_tool(
                "ticket__create_ticket", {"title": f"conc-{i}", "body": "b"}
            )
            assert not result.isError
            return payload(result)["id"]

        ids = await asyncio.gather(*(call(i) for i in range(10)))
        assert len(set(ids)) == 10


async def test_ac6_backend_kill_then_auto_reconnect(backends):
    async with gateway() as url, mcp_client(url) as session:
        ok = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not ok.isError

        backends["ops"].stop()
        down = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert down.isError
        assert json.loads(down.content[0].text) == {"code": "BACKEND_UNAVAILABLE", "server": "ops"}

        backends["ops"].start()
        up = await session.call_tool("ops__get_metrics", {"metric": "cpu"})
        assert not up.isError  # 자동 재연결


async def test_ac7_lazy_reaggregation_when_backend_starts_late(backends):
    backends["docs"].stop()
    async with gateway() as url, mcp_client(url) as session:
        # 기동 성공 + 살아있는 백엔드 tool은 정상 동작
        tools = {t.name for t in (await session.list_tools()).tools}
        assert "docs__search_docs" not in tools
        ok = await session.call_tool("ticket__search_tickets", {"query": "x"})
        assert not ok.isError

        backends["docs"].start()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert tools == EXPECTED_PREFIXED  # 지연 재집계로 docs 등장
        doc = await session.call_tool("docs__search_docs", {"query": "deployment"})
        assert not doc.isError
