"""백엔드 세션 관리 — 백엔드당 MCP 세션 1개를 재사용한다. 커넥션 풀이 아니다.

연결 컨텍스트(streamablehttp_client/ClientSession)는 anyio cancel scope를
품고 있어 연 task에서만 닫을 수 있다. 그래서 연결마다 소유 task를 하나 띄워
컨텍스트 진입·탈출을 그 task 안에서만 수행하고, 요청 task들은 session 객체만
공유한다 (Day-1 스파이크로 동시 호출 안전성 검증됨 — scripts/spike_concurrency.py).
"""

import asyncio
import logging

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from gateway import errors

logger = logging.getLogger(__name__)


class Backend:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.tools: list[types.Tool] | None = None  # None = 미집계 (지연 재집계 대상)
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._lock = asyncio.Lock()  # 연결/재연결 직렬화 — 세션 중복 생성 방지

    async def _connection_task(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """연결 컨텍스트의 소유 task. stop 신호까지 세션을 유지한다."""
        try:
            async with streamablehttp_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.tools = (await session.list_tools()).tools
                    self._session = session
                    ready.set_result(None)
                    await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else ConnectionError(str(e)))
        finally:
            self._session = None

    async def _connect_locked(self) -> None:
        stop = asyncio.Event()
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._connection_task(ready, stop))
        try:
            await ready
        except BaseException:
            stop.set()
            raise
        self._task, self._stop = task, stop

    async def _teardown_locked(self) -> None:
        if self._task is not None:
            self._stop.set()
            try:
                await self._task
            except Exception:
                pass
            self._task, self._stop = None, None
        self._session = None

    async def ensure_session(self) -> ClientSession:
        """살아있는 세션 반환 — 없으면 연결 + tool 집계. 실패 시 예외 전파."""
        async with self._lock:
            if self._session is None:
                await self._teardown_locked()  # 죽은 task 잔재 정리
                await self._connect_locked()
            return self._session

    async def _race_call(
        self, session: ClientSession, owner: asyncio.Task, tool: str, arguments: dict
    ) -> types.CallToolResult:
        """call_tool을 연결 소유 task 종료와 경주 — 연결이 죽으면 즉시 실패.

        백엔드가 죽으면 transport task group이 취소로 무너지는데, 이때 세션의
        pending 요청 통지도 같이 취소돼 call_tool이 영원히 깨어나지 못할 수 있다.
        """
        call = asyncio.ensure_future(session.call_tool(tool, arguments))
        done, _ = await asyncio.wait({call, owner}, return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            return call.result()
        call.cancel()
        raise ConnectionError(f"backend {self.name} connection lost")

    async def call(self, tool: str, arguments: dict) -> types.CallToolResult:
        """tools/call 중계. 전송 오류 시 재연결 1회 재시도 후 BACKEND_UNAVAILABLE."""
        try:
            session = await self.ensure_session()
            return await self._race_call(session, self._task, tool, arguments)
        except Exception:
            async with self._lock:
                await self._teardown_locked()
                try:
                    await self._connect_locked()
                except Exception:
                    logger.warning("backend %s unavailable", self.name)
                    return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)
                session = self._session
            try:
                return await self._race_call(session, self._task, tool, arguments)
            except Exception:
                async with self._lock:
                    await self._teardown_locked()
                logger.warning("backend %s unavailable after reconnect", self.name)
                return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)

    async def close(self) -> None:
        async with self._lock:
            await self._teardown_locked()
