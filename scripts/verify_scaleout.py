"""scale-out 증거 수집 — 한 줄 실행으로 세 가지를 측정한다 (T7 / 결정 6A).

[무엇을 재나]
  A. rate limit 실효 한도 — 파드 로컬 토큰 버킷이 레플리카 수만큼 배증하는가 (findings §3)
  B. ticket-server 소실   — replicas 3에서 HIT / SILENT_MISS / LOUD의 비율 (findings §5)
  C. 세션 전략 차이       — replicas 3에서 stateful vs stateless의 e2e 통과 수 (findings §1-A)

[측정만 한다 — 결정하지 않는다]
§3을 어떻게 고칠지(Redis / 한도를 레플리카 수로 나눔 / 게이트웨이 앞단 이동)는 이 스크립트의
일이 아니다. 여기가 뽑는 것은 "실효 한도가 몇이냐"는 숫자 하나고, 그 숫자로 무엇을 할지는
findings 문서가 정한다. T5·T6이 손으로 잰 것을 재현 가능한 형태로 옮긴 것이기도 하다 —
결론이 아니라 결론의 근거가 매번 같은 명령으로 다시 나와야 한다는 것이 6A다.

[클러스터를 건드리고 되돌린다]
replicas와 env를 바꿔야 재현되므로 kubectl로 실제 클러스터를 조작한다. 끝나면(예외로 죽어도)
finally에서 기준선 — 게이트웨이·ticket 둘 다 `replicas: 1`, 토글 3종 제거 — 로 되돌린다.
매니페스트 파일은 건드리지 않는다(T5·T6과 같은 규칙).

[왜 GATEWAY_RATE_REFILL=0인가]
`ratelimit.py:44`의 refill 기본값은 capacity다(1초면 통이 가득 찬다). 그대로 두면 측정 중
계속 회복돼 "몇 번 통과했나"가 시간에 의존한다. refill을 0으로 묶으면 토큰이 회복되지 않아
**통과한 횟수가 곧 실효 한도**가 된다.

실행: GATEWAY_JWT_SECRET=<secret> GATEWAY_URL=http://localhost:8080/mcp \
        uv run --project gateway python scripts/verify_scaleout.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from issue_tokens import issue_token  # e2e_demo와 같은 이유 — 발급과 검증이 한 함수를 공유
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080/mcp")
AGENT = "support-agent"  # docs 읽기·ticket 쓰기가 정책상 허용된 에이전트(policy.yaml)

RATE_CAPACITY = 5  # GATEWAY_RATE_LIMIT. replicas 3에서 15가 나오면 배증이 증명된다
RATE_PROBES = 30  # 한도(5·15)보다 충분히 커야 "거부로 끝났다"를 관측할 수 있다
TICKET_TRIALS = 12  # §5가 search-only를 12회 돌려 SILENT_MISS 2건을 봤다 — 같은 표본 크기
E2E_RUNS = 6  # §1·§1-A가 센 횟수


# --- 클러스터 조작 -------------------------------------------------------------


def kubectl(*args: str) -> str:
    """kubectl 한 번. 실패는 조용히 넘기지 않는다 — 측정이 조작 실패 위에서 돌면 안 된다."""
    p = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def settle(deploy: str, replicas: int) -> None:
    """파드 목록이 '준비된 새 파드 정확히 N개'로 정착할 때까지 기다린다.

    [왜 rollout status로 부족한가 — 실측으로 배운 것]
    `kubectl rollout status`는 옛 파드가 **삭제 표시**되는 순간 성공을 반환한다. ReplicaSet이
    active 파드를 셀 때 `deletionTimestamp`가 찍힌 파드를 빼기 때문이다. 그런데 그 파드는
    graceful termination 동안 **계속 요청을 받는다**(traefik의 EndpointSlice 반영도 즉시가
    아니다). 그래서 rollout status 직후에 세션을 열면 죽어가는 파드에 붙고, 몇 초 뒤
    `McpError: Session terminated`로 측정이 통째로 날아간다. 실제로 겪었다.
    """
    for _ in range(120):
        pods = json.loads(kubectl("get", "pods", "-l", f"app={deploy}", "-o", "json"))["items"]
        # 종료 중인 파드도 목록에는 남아 있다 — 총 개수가 replicas여야 '옛 파드가 없다'는 뜻.
        ready = [
            p
            for p in pods
            if all(c.get("ready") for c in p["status"].get("containerStatuses", []))
            and not p["metadata"].get("deletionTimestamp")
        ]
        if len(pods) == replicas == len(ready):
            return
        time.sleep(1)
    raise RuntimeError(f"{deploy}: 파드가 {replicas}개로 정착하지 않았다")


def rollout(deploy: str, replicas: int, **env: str | None) -> None:
    """env를 맞추고 replicas로 스케일한 뒤 롤아웃이 실제로 끝날 때까지 기다린다. env 값 None이면 제거.

    set env가 새 ReplicaSet을 만들므로 순서가 중요하다 — env를 먼저 바꾸고 스케일해야
    새 파드가 처음부터 원하는 설정으로 뜬다(옛 설정 파드로 몇 번 측정하는 사고 방지).
    """
    if env:
        pairs = [f"{k}={v}" if v is not None else f"{k}-" for k, v in env.items()]
        kubectl("set", "env", f"deployment/{deploy}", *pairs)
    kubectl("scale", f"deployment/{deploy}", f"--replicas={replicas}")
    kubectl("rollout", "status", f"deployment/{deploy}", "--timeout=180s")
    settle(deploy, replicas)


def restore() -> None:
    """기준선 복귀 — replicas 1, 토글 3종 제거. 예외로 죽어도 반드시 지나가야 하는 경로."""
    rollout(
        "gateway",
        1,
        GATEWAY_RATE_LIMIT=None,
        GATEWAY_RATE_REFILL=None,
        GATEWAY_MCP_STATELESS=None,
    )
    rollout("ticket-server", 1)


# --- MCP 호출 ------------------------------------------------------------------


def _headers() -> dict:
    return {"Authorization": f"Bearer {issue_token(AGENT, os.environ['GATEWAY_JWT_SECRET'])}"}


def error_code(result) -> str | None:
    """isError 결과의 구조화 code. 정상 결과면 None."""
    if not result.isError:
        return None
    try:
        return json.loads(result.content[0].text).get("code")
    except (ValueError, AttributeError, IndexError):
        return "UNPARSEABLE"


async def call_once(tool: str, args: dict):
    """매 호출마다 게이트웨이에 새로 붙는다 — §5의 측정 방식(시도끼리 세션을 공유하지 않는다)."""
    async with streamablehttp_client(GATEWAY_URL, headers=_headers()) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return await session.call_tool(tool, args)


# --- A. rate limit --------------------------------------------------------------


async def measure_rate() -> tuple[int, int]:
    """세션 하나로 RATE_PROBES회 호출하고 (통과, 거부)를 센다. refill=0이므로 통과 수 = 실효 한도.

    tool은 아무거나 무방하다 — rate limit은 `route_call` 0단계라 tool 해석·정책보다 먼저 걸린다.
    실제로 백엔드까지 가는 docs 검색을 쓰는 이유는 통과 판정이 '진짜 통과'여야 하기 때문이다.
    """
    allowed = limited = 0
    async with streamablehttp_client(GATEWAY_URL, headers=_headers()) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            for _ in range(RATE_PROBES):
                result = await session.call_tool("docs__search_docs", {"query": "deployment"})
                if error_code(result) == "RATE_LIMITED":
                    limited += 1
                else:
                    allowed += 1
    return allowed, limited


# --- B. ticket-server -----------------------------------------------------------


def classify(result) -> str:
    """HIT / SILENT_MISS / LOUD — §5의 판정 기준 그대로.

    SILENT_MISS가 이 측정의 표적이다: isError가 아닌데 결과가 비어 있는 것. 에이전트 입장에선
    "그런 티켓 없음"과 구별되지 않는다.
    """
    if result.isError:
        return "LOUD"
    hits = (result.structuredContent or {}).get("result", result.content)
    return "HIT" if hits else "SILENT_MISS"


async def measure_ticket() -> tuple[bool, Counter, Counter]:
    """티켓 하나를 심고 search-only를 반복해 세 결과의 비율을 센다."""
    marker = f"t7-{os.getpid()}"
    seeded = False
    for _ in range(TICKET_TRIALS):
        # LOUD가 지배적이라 한 번에 안 들어간다 — 심을 때까지 반복한다.
        if not (await call_once("ticket__create_ticket", {"title": marker, "body": "T7"})).isError:
            seeded = True
            break
    counts: Counter = Counter()
    codes: Counter = Counter()
    for _ in range(TICKET_TRIALS):
        result = await call_once("ticket__search_tickets", {"query": marker})
        counts[classify(result)] += 1
        if result.isError:
            codes[error_code(result)] += 1
    return seeded, counts, codes


# 파드가 실제로 무엇을 갖고 있나. 판정 기준은 파일 존재가 아니라 **테이블 유무**다 —
# sqlite3.connect가 없는 파일을 만들어 버려 측정이 오염되기 때문(§5의 주의). mode=ro로 연다.
_STORAGE_PROBE = """
import os, sqlite3
p = "/app/tickets.db"
if not os.path.exists(p):
    print("db=no")
else:
    c = sqlite3.connect("file:" + p + "?mode=ro", uri=True)
    if c.execute("select name from sqlite_master where name='tickets'").fetchall():
        print("rows=%d" % c.execute("select count(*) from tickets").fetchone()[0])
    else:
        print("table=no")
"""


def ticket_storage() -> list[str]:
    """ticket-server 파드별 저장소 스냅샷 — 쓰기가 어디로 갔고 읽기가 어디서 돌았나."""
    pods = kubectl(
        "get", "pods", "-l", "app=ticket-server", "-o", "jsonpath={.items[*].metadata.name}"
    ).split()
    out = []
    for pod in pods:
        p = subprocess.run(
            ["kubectl", "exec", pod, "--", "python", "-c", _STORAGE_PROBE],
            capture_output=True,
            text=True,
            timeout=60,
        )
        out.append(f"{pod.rsplit('-', 1)[-1]} {p.stdout.strip() or 'probe failed'}")
    return out


# --- C. 세션 전략 ---------------------------------------------------------------


def measure_session() -> int:
    """e2e_demo.py를 그대로 E2E_RUNS회 돌려 통과 수를 센다.

    자체 시나리오를 새로 짜지 않는 이유: §1-A가 잰 것이 바로 이 스크립트의 exit code이고,
    측정 대상이 달라지면 과거 수치와 비교가 안 된다.
    """
    script = str(Path(__file__).with_name("e2e_demo.py"))
    env = {**os.environ, "GATEWAY_URL": GATEWAY_URL}
    return sum(
        subprocess.run(
            [sys.executable, script], capture_output=True, text=True, env=env, timeout=180
        ).returncode
        == 0
        for _ in range(E2E_RUNS)
    )


# --- 진행 ------------------------------------------------------------------------


async def main() -> int:
    # Windows 콘솔·파이프의 기본 인코딩(cp949)은 '—'를 못 찍어 리포트가 통째로 죽는다.
    # 이 스크립트의 산출물은 findings에 붙일 한글 텍스트라 UTF-8로 고정한다.
    # line_buffering: 롤아웃 대기가 길어서(수 분) 섹션이 끝나는 대로 보여야 한다 —
    # 파일·파이프로 받으면 기본이 블록 버퍼라 끝날 때까지 아무것도 안 나온다.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if not os.environ.get("GATEWAY_JWT_SECRET"):
        print("GATEWAY_JWT_SECRET이 필요하다 (게이트웨이 Secret과 같은 값).")
        return 1
    print(f"[verify] gateway={GATEWAY_URL}")
    try:
        print(f"\n=== A. rate limit — 실효 한도 (LIMIT={RATE_CAPACITY}, REFILL=0) ===")
        rollout(
            "gateway",
            1,
            GATEWAY_RATE_LIMIT=str(RATE_CAPACITY),
            GATEWAY_RATE_REFILL="0",
            GATEWAY_MCP_STATELESS=None,
        )
        one = await measure_rate()
        print(f"  replicas=1              : {RATE_PROBES}회 중 통과={one[0]} 거부={one[1]}")
        # replicas 3에서 요청이 실제로 흩어지려면 stateless가 필요하다(T5 결론) — 안 그러면
        # rate limit이 아니라 세션이 먼저 깨져 무엇을 쟀는지 알 수 없게 된다.
        rollout("gateway", 3, GATEWAY_MCP_STATELESS="1")
        three = await measure_rate()
        ratio = three[0] / one[0] if one[0] else float("nan")
        print(f"  replicas=3 + stateless  : {RATE_PROBES}회 중 통과={three[0]} 거부={three[1]}")
        print(f"  → 실효 한도 {one[0]} → {three[0]} (×{ratio:.1f})")

        print(f"\n=== B. ticket-server — replicas=3 소실 (search-only ×{TICKET_TRIALS}) ===")
        rollout(
            "gateway",
            1,
            GATEWAY_RATE_LIMIT=None,
            GATEWAY_RATE_REFILL=None,
            GATEWAY_MCP_STATELESS=None,
        )
        rollout("ticket-server", 3)
        seeded, counts, codes = await measure_ticket()
        print(f"  티켓 심기               : {'성공' if seeded else '실패(전부 LOUD)'}")
        print(
            f"  HIT={counts['HIT']} SILENT_MISS={counts['SILENT_MISS']} LOUD={counts['LOUD']}"
            f"  {dict(codes) if codes else ''}"
        )
        print(f"  파드별 저장소           : {' / '.join(ticket_storage())}")
        rollout("ticket-server", 1)

        print(f"\n=== C. 세션 전략 — replicas=3에서 e2e ×{E2E_RUNS} ===")
        rollout("gateway", 3)
        stateful = measure_session()
        print(f"  stateful (기본)         : PASS={stateful} FAIL={E2E_RUNS - stateful}")
        rollout("gateway", 3, GATEWAY_MCP_STATELESS="1")
        stateless = measure_session()
        print(f"  stateless (전략 B)      : PASS={stateless} FAIL={E2E_RUNS - stateless}")
    finally:
        print("\n[verify] 기준선 복귀 중 (replicas 1, 토글 제거)")
        restore()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
