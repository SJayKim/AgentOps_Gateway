"""백엔드 세션 관리 — 백엔드당 MCP 세션 1개를 열어 재사용한다. 커넥션 풀이 아니다.

[왜 풀이 아니라 단일 세션 + 소유 task인가 — 이 파일에서 가장 중요한 설계]
MCP 클라이언트 연결 컨텍스트(streamablehttp_client / ClientSession)는 내부에 anyio
cancel scope를 품고 있다. anyio cancel scope에는 강한 규칙이 있다: '진입한 task에서만
탈출(close)할 수 있다'. 만약 요청을 처리하는 여러 task가 같은 컨텍스트를 자유롭게
enter/exit하면 "cancel scope in different task" 런타임 오류가 난다.

그래서 구조를 이렇게 잡았다:
  - 연결마다 '소유 task'를 딱 하나 띄운다(_connection_task). 컨텍스트 진입·유지·탈출은
    전부 이 task '안에서만' 일어난다.
  - 실제 요청을 처리하는 task들은 컨텍스트를 건드리지 않고 session 객체만 공유해서 쓴다.
  - 소유 task는 stop 이벤트가 올 때까지 그냥 대기하며 연결을 살려 둔다.
이 동시 호출 안전성은 Day-1 스파이크로 검증했다(scripts/spike_concurrency.py).

[tools is None의 의미]
한 번도 성공적으로 집계 못 한 상태. 기동 시 죽어 있던 백엔드가 이 상태로 남고,
aggregate/route가 이걸 보고 지연 재집계를 시도한다.
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
        self.tools: list[types.Tool] | None = None  # None = 미집계(지연 재집계 대상)
        self._session: ClientSession | None = None  # 요청 task들이 공유해 쓰는 살아있는 세션
        self._task: asyncio.Task | None = None  # 연결 컨텍스트를 소유·유지하는 task
        self._stop: asyncio.Event | None = None  # 소유 task에게 "이제 닫아라"를 알리는 신호
        self._lock = (
            asyncio.Lock()
        )  # 연결/재연결을 직렬화 — 동시 요청이 세션을 중복 생성하지 못하게

    async def _connection_task(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """연결 컨텍스트의 '소유 task'. 진입·초기화·tool 집계 후 stop까지 세션을 살려 둔다.

        ready future로 "연결 성공/실패"를 호출자에게 알리고, 그 다음엔 stop.wait()로
        멈춰 서서 컨텍스트를 열어 둔 채 대기만 한다. 컨텍스트의 close는 이 task가 stop을
        받고 빠져나올 때 with 블록 종료로 자연히 일어난다(다른 task가 닫지 않는다).
        """
        try:
            async with streamablehttp_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()  # MCP 핸드셰이크
                    self.tools = (await session.list_tools()).tools  # 이 백엔드의 tool 목록 캐시
                    self._session = session  # 요청 task들이 쓸 수 있도록 공개
                    ready.set_result(None)  # "연결 성공" 통지 → 대기 중인 _connect_locked 깨움
                    await stop.wait()  # close 신호가 올 때까지 컨텍스트를 열어 둔 채 대기
        except BaseException as e:
            # 연결/초기화 도중 실패 → 아직 ready를 못 채웠으면 예외를 실어 호출자에게 전파.
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else ConnectionError(str(e)))
        finally:
            self._session = None  # task가 끝나면 세션은 더 이상 유효하지 않다

    async def _connect_locked(self) -> None:
        """소유 task를 띄우고 연결이 준비될 때까지 기다린다. (_lock을 쥔 상태에서만 호출)"""
        stop = asyncio.Event()
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._connection_task(ready, stop))
        try:
            await ready  # 연결 성공(None) 또는 실패(예외)까지 블록
        except BaseException:
            stop.set()  # 실패 시 떠 있는 소유 task에게 정리하고 끝내라고 신호
            raise
        self._task, self._stop = task, stop  # 성공 → 핸들 보관

    async def _teardown_locked(self) -> None:
        """현재 연결을 깨끗이 닫는다. 소유 task에 stop을 주고 그 task가 끝나길 기다린다."""
        if self._task is not None:
            self._stop.set()  # 소유 task의 stop.wait()를 깨워 with 블록을 종료시킴
            try:
                await self._task  # 소유 task가 컨텍스트를 닫고 완전히 끝날 때까지 대기
            except Exception:
                pass  # 닫는 도중의 잔여 예외는 무시 — 어차피 폐기 중인 연결
            self._task, self._stop = None, None
        self._session = None

    async def ensure_session(self) -> ClientSession:
        """살아있는 세션을 반환. 없으면 연결 + tool 집계까지 하고, 실패 시 예외를 전파한다.

        _lock으로 감싸 동시 요청이 들어와도 연결은 한 번만 생성된다(나머지는 락 뒤에서
        기다렸다가 이미 만들어진 세션을 받는다).
        """
        async with self._lock:
            if self._session is None:
                await self._teardown_locked()  # 죽은 task 잔재가 있으면 먼저 청소
                await self._connect_locked()
            return self._session

    async def _race_call(
        self, session: ClientSession, owner: asyncio.Task, tool: str, arguments: dict
    ) -> types.CallToolResult:
        """call_tool을 '연결 소유 task의 종료'와 경주시킨다 — 연결이 죽으면 즉시 실패시킨다.

        [왜 경주가 필요한가]
        백엔드가 갑자기 죽으면 transport의 task group이 취소로 무너지는데, 이때 세션이
        기다리던 응답 통지도 함께 취소돼 call_tool이 '영원히 깨어나지 못하는' 교착에 빠질
        수 있다. 그래서 call_tool과 소유 task(owner)를 동시에 기다려, 소유 task가 먼저
        끝나면(=연결이 죽음) call을 취소하고 ConnectionError로 빠르게 실패한다.
        """
        call = asyncio.ensure_future(session.call_tool(tool, arguments))
        done, _ = await asyncio.wait({call, owner}, return_when=asyncio.FIRST_COMPLETED)
        if call in done:
            return call.result()  # 정상적으로 백엔드 응답이 먼저 도착
        call.cancel()  # 소유 task가 먼저 끝남 = 연결 소실 → 매달린 call을 취소
        raise ConnectionError(f"backend {self.name} connection lost")

    async def call(self, tool: str, arguments: dict) -> types.CallToolResult:
        """tools/call을 백엔드로 중계. 전송 오류 시 재연결 1회 재시도 후 BACKEND_UNAVAILABLE.

        재시도 정책을 '1회'로 둔 이유: 무한/다회 재시도는 죽은 백엔드에 대한 요청을 길게
        매달아 latency를 악화시키고 장애를 전파한다. 한 번 재연결해 보고 그래도 안 되면
        구조화된 BACKEND_UNAVAILABLE을 빠르게 돌려주는 게 데모·운영 모두에 낫다.
        """
        try:
            session = await self.ensure_session()
            return await self._race_call(session, self._task, tool, arguments)
        except Exception:
            # 첫 시도 실패(전송 오류/연결 소실) → 락 안에서 완전히 끊고 다시 연결을 시도한다.
            async with self._lock:
                await self._teardown_locked()
                try:
                    await self._connect_locked()
                except Exception:
                    logger.warning("backend %s unavailable", self.name)
                    return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)
                session = self._session
            try:
                # 재연결 성공 → 한 번 더 호출 시도.
                return await self._race_call(session, self._task, tool, arguments)
            except Exception:
                # 재연결 직후에도 실패 → 포기. 깔끔히 끊고 BACKEND_UNAVAILABLE 반환.
                async with self._lock:
                    await self._teardown_locked()
                logger.warning("backend %s unavailable after reconnect", self.name)
                return errors.error_result("BACKEND_UNAVAILABLE", server=self.name)

    async def close(self) -> None:
        """앱 종료 시 연결을 닫는다(lifespan에서 호출). 락으로 동시 teardown과 경합 방지."""
        async with self._lock:
            await self._teardown_locked()
