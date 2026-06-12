"""메트릭·trace 설정 — S5 P1/P3.

메트릭 기록은 record_call 하나로 — audit 기록 지점과 동일한 곳에서 호출해
두 시스템이 같은 사실을 보게 한다 (decision enum 4종 공유).
tracer provider는 프로세스당 1회만 설치 (테스트가 build_app을 반복 호출).
exporter는 콘솔(기본) — Jaeger/Tempo는 exporter 교체로만 가능한 구조면 충분.
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

TOOL_CALLS = Counter(
    "gateway_tool_calls_total",
    "tools/call count by final decision",
    ["agent", "server", "tool", "decision"],  # decision: allowed|denied|auth_failed|error
)
CALL_DURATION = Histogram(
    "gateway_tool_call_duration_seconds",
    "tools/call routing+relay duration (p50/p99 도출용)",
    ["server", "tool"],
)
POLICY_DENIED = Counter(
    "gateway_policy_denied_total",
    "policy-denied tools/call count — 핵심 메트릭, 절대 미삭제",
    ["agent", "server", "tool"],
)

_provider_installed = False


def tracer() -> trace.Tracer:
    global _provider_installed
    if not _provider_installed:
        provider = TracerProvider()
        # Simple(동기) — Batch는 atexit flush가 닫힌 stdout에 써서 테스트를 깨뜨림
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _provider_installed = True
    return trace.get_tracer("gateway")


def trace_id_hex(span: trace.Span) -> str:
    """현재 span의 trace ID — audit JSONL·게이트웨이 로그가 공유하는 32 hex."""
    return format(span.get_span_context().trace_id, "032x")


def record_call(
    *, agent: str, server: str, tool: str, decision: str, duration_s: float | None = None
) -> None:
    TOOL_CALLS.labels(agent=agent, server=server, tool=tool, decision=decision).inc()
    if decision == "denied":
        POLICY_DENIED.labels(agent=agent, server=server, tool=tool).inc()
    if duration_s is not None:
        CALL_DURATION.labels(server=server, tool=tool).observe(duration_s)


def metrics_payload() -> tuple[bytes, str]:
    """(/metrics body, content-type) — Prometheus 스크레이프 응답."""
    return generate_latest(), CONTENT_TYPE_LATEST
