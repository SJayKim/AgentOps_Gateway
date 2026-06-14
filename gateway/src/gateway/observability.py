"""메트릭·trace 설정 — S5(관측성)의 P1/P3.

[관측성이 이 프로젝트의 차별점]
사용자가 4주 풀플랜(Approach B)을 고른 이유가 관측성 스택 딥다이브였다(design.md
"Recommended Approach"). 그래서 정책 거부 카운트 메트릭은 "절대 빼지 않는" 핵심
산출물이다(전제 4). 이 모듈이 Prometheus 메트릭과 OpenTelemetry trace를 함께 제공한다.

[메트릭과 audit이 같은 사실을 보게 한다]
메트릭 기록(record_call)은 audit 기록과 '같은 호출 지점'(app.py call_tool)에서, '같은
decision enum 4종'으로 일어난다. 두 시스템이 어긋나지 않도록 어휘를 공유시킨 것이다.

[tracer provider를 프로세스당 1회만 설치하는 이유]
테스트가 build_app()을 반복 호출한다. provider를 매번 새로 설치하면 OTel이 중복 설치
경고를 내거나 span이 엉킨다. 그래서 모듈 전역 플래그로 최초 1회만 설치한다.
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# [메트릭 1] 모든 tools/call을 최종 decision별로 센다. 라벨 카디널리티(agent×server×tool×
# decision)가 데모 규모(3×3×~3×4)라 폭발하지 않는다 — 그래서 tool까지 라벨에 둘 수 있다.
TOOL_CALLS = Counter(
    "gateway_tool_calls_total",
    "tools/call count by final decision",
    ["agent", "server", "tool", "decision"],  # decision: allowed|denied|auth_failed|error
)
# [메트릭 2] 라우팅+중계 소요 시간 히스토그램. p50/p99 latency를 Grafana에서 도출하는 근거.
# agent를 라벨에서 뺀 것은 latency가 어느 백엔드/tool이 느린지의 문제지 누가 불렀냐가
# 아니기 때문 — 카디널리티도 줄인다.
CALL_DURATION = Histogram(
    "gateway_tool_call_duration_seconds",
    "tools/call routing+relay duration (p50/p99 도출용)",
    ["server", "tool"],
)
# [메트릭 3] 정책 거부 전용 카운터. TOOL_CALLS에서 decision="denied"로도 구할 수 있지만,
# 이 메트릭은 design.md 전제 4의 "핵심 산출물"이라 대시보드·알림에서 1급으로 다루기 위해
# 독립 메트릭으로 따로 둔다. (주석의 "절대 미삭제"는 그 의지의 표시.)
POLICY_DENIED = Counter(
    "gateway_policy_denied_total",
    "policy-denied tools/call count — 핵심 메트릭, 절대 미삭제",
    ["agent", "server", "tool"],
)

_provider_installed = False  # tracer provider 1회 설치 가드 (위 docstring 참조)


def tracer() -> trace.Tracer:
    """게이트웨이용 OTel Tracer를 반환. 최초 호출 시 provider를 lazy 설치한다."""
    global _provider_installed
    if not _provider_installed:
        provider = TracerProvider()
        # Simple(동기) processor를 쓴다. Batch processor는 백그라운드 스레드에서 atexit에
        # flush하는데, 그 시점엔 테스트가 stdout을 이미 닫아 ConsoleExporter가 깨진다.
        # 동기 export는 데모엔 충분하고 테스트도 안 깨뜨린다.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _provider_installed = True
    return trace.get_tracer("gateway")


def trace_id_hex(span: trace.Span) -> str:
    """현재 span의 trace ID를 32자리 hex 문자열로. audit JSONL·게이트웨이 로그가 공유하는 키."""
    return format(span.get_span_context().trace_id, "032x")


def record_call(
    *, agent: str, server: str, tool: str, decision: str, duration_s: float | None = None
) -> None:
    """호출 1건의 메트릭을 한 곳에서 기록한다 (app.py가 audit.record와 나란히 호출)."""
    TOOL_CALLS.labels(agent=agent, server=server, tool=tool, decision=decision).inc()
    if decision == "denied":
        POLICY_DENIED.labels(
            agent=agent, server=server, tool=tool
        ).inc()  # 거부면 핵심 메트릭도 증가
    if duration_s is not None:
        # 인증 실패 등 백엔드까지 못 간 호출은 duration이 None — latency 통계를 오염시키지
        # 않도록 측정된 경우에만 관측한다.
        CALL_DURATION.labels(server=server, tool=tool).observe(duration_s)


def metrics_payload() -> tuple[bytes, str]:
    """(/metrics 응답 body, content-type). Prometheus 스크레이프가 그대로 받아 파싱한다."""
    return generate_latest(), CONTENT_TYPE_LATEST
