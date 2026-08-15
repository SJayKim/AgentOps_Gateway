# AgentOps Gateway

## Scope and layout

- `gateway/`: FastMCP gateway, authentication, policy, audit, and metrics.
- `servers/{ticket,docs,ops}/`: backend MCP servers.
- `demo-agent/`: optional LangGraph demonstration agent.
- `tests/unit/` and `tests/integration/`: pytest suites; shared server lifecycle helpers live in `tests/integration/helpers.py`.
- `policies/policy.yaml`: default-deny authorization policy. `docs/specs/` is the product contract.

## Commands

Run from the repository root with Python 3.12 and `uv`:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose up -d --build --wait
GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/e2e_demo.py
```

Do not use watch mode in automated validation. Never commit real JWT secrets, API keys, credentials, certificates, or audit output.

## Invariants

- Preserve the gateway as the only public MCP entry point and keep policy default-deny.
- Keep audit records append-only and correlate requests with `trace_id`; do not log full sensitive arguments.
- With MCP SDK 1.27, a FastMCP tool returning a bare `dict` may leave `structuredContent` empty. Integration assertions must consume responses through `tests/integration/helpers.py::payload`.
- Tests that start more than one in-process uvicorn server must reset `sse_starlette.sse.AppStatus.should_exit` and `should_exit_event` immediately before each start.
- Make narrow changes that preserve package boundaries and the existing tool names/contracts.

## Done criteria

- Relevant unit and integration tests pass.
- `uv run ruff check .` and `uv run ruff format --check .` pass.
- Gateway/auth/policy changes are exercised by an allowed and a denied path; compose or E2E changes also pass `scripts/e2e_demo.py`.

