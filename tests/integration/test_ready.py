"""T3 — /ready 능동 probe. 실제 백엔드 subprocess를 죽였다 살리며 상태 전환을 본다.

[왜 폴링으로 단언하나]
백엔드가 죽은 사실은 즉시 관측되지 않는다. 소켓이 닫히면 연결 소유 task가 풀리면서
upstream.py:64-65의 finally가 _session을 None으로 되돌리는데, 그건 이벤트 루프가 돌아야
일어난다. BackendProc.stop()은 블로킹이라 그 사이 루프가 멈춰 있다. readiness는 원래
'곧' 반영되는 성질이고(kubelet도 periodSeconds마다 다시 묻는다), 전환이 끝내 안 오면
데드라인에서 마지막 응답을 그대로 돌려줘 단언이 실패한다.
"""

import asyncio
import time

import httpx
from helpers import gateway


async def poll_ready(client: httpx.AsyncClient, predicate, timeout: float = 10.0):
    """predicate가 참이 되는 /ready 응답을 돌려준다. 데드라인을 넘기면 마지막 응답 그대로."""
    deadline = time.monotonic() + timeout
    while True:
        response = await client.get("/ready")
        if predicate(response) or time.monotonic() > deadline:
            return response
        await asyncio.sleep(0.1)


def all_connected(response) -> bool:
    return response.status_code == 200 and all(response.json()["backends"].values())


async def test_ready_flips_to_503_when_all_backends_die_then_back_to_200(backends):
    """전멸 → 503, 복구 → 200. T3의 최우선 회귀 테스트.

    복구가 되는 것 자체가 '능동 probe'의 증거다. backend.tools 수동 체크였다면 재연결
    트리거가 요청뿐이라 NotReady 파드는 영영 못 돌아온다.
    """
    async with gateway() as url:
        async with httpx.AsyncClient(base_url=url.removesuffix("/mcp")) as client:
            up = await client.get("/ready")
            assert up.status_code == 200
            assert up.json() == {
                "status": "ready",
                "backends": {"ticket": True, "docs": True, "ops": True},
            }

            for proc in backends.values():
                proc.stop()
            down = await poll_ready(client, lambda r: r.status_code == 503)
            assert down.status_code == 503
            assert down.json() == {
                "status": "not ready",
                "backends": {"ticket": False, "docs": False, "ops": False},
            }

            for proc in backends.values():
                proc.start()
            back = await poll_ready(client, all_connected)
            assert back.status_code == 200
            assert back.json()["backends"] == {"ticket": True, "docs": True, "ops": True}


async def test_ready_stays_200_when_one_backend_dies(backends):
    """백엔드 1개가 죽어도 Ready를 유지한다 — '부분 가용성 > 전체 다운'(eng review T1).

    여기서 503을 내면 ops 하나 때문에 게이트웨이 파드가 로드밸런서에서 빠져 ticket·docs로
    가는 멀쩡한 요청까지 함께 죽는다. 이 테스트가 그 결정을 고정한다.
    """
    async with gateway() as url:
        async with httpx.AsyncClient(base_url=url.removesuffix("/mcp")) as client:
            backends["ops"].stop()
            partial = await poll_ready(client, lambda r: r.json()["backends"]["ops"] is False)
            assert partial.status_code == 200
            assert partial.json()["backends"] == {"ticket": True, "docs": True, "ops": False}

            backends["ops"].start()


async def test_health_stays_unconditional(backends):
    """/health는 그대로 무조건 ok — compose healthcheck 계약을 T3가 바꾸지 않는다."""
    async with gateway() as url:
        async with httpx.AsyncClient(base_url=url.removesuffix("/mcp")) as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
