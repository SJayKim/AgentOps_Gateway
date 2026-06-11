# S1: 프로젝트 스캐폴드 — uv workspace + CI + compose 뼈대

> Epic: `00-epic.md` · 의존: 없음 · Effort: ~1일

## Context

레포는 그린필드 (2026-06-11 검증: 추적 파일은 `.claude/`, `CLAUDE.md`, `docs/` 뿐). S2-S6 전부가 이 스캐폴드 위에서 작업하므로, 패키지 구조·테스트·CI를 여기서 한 번에 고정해 이후 spec들이 셋업 결정을 다시 내리지 않게 한다.

## Current State

- Python 코드, `pyproject.toml`, CI 설정 전부 없음
- `.claude/settings.json` + `.claude/hooks/protect-files.sh` (보안 hook, jq 불필요·sed 기반) 존재 — 건드리지 않는다

## Proposed Change

uv workspace 모노레포를 만든다. 패키지 4개 + 루트 통합 테스트.

### 디렉토리 구조 (최종형)

```
AgentOps_Gateway/
├── pyproject.toml              # workspace 루트 (members 선언, dev 의존성: pytest, ruff)
├── uv.lock
├── gateway/
│   ├── pyproject.toml          # name: agentops-gateway
│   └── src/gateway/__init__.py
├── servers/
│   ├── ticket/
│   │   ├── pyproject.toml      # name: ticket-server
│   │   └── src/ticket_server/__init__.py
│   ├── docs/
│   │   ├── pyproject.toml      # name: docs-server
│   │   └── src/docs_server/__init__.py
│   └── ops/
│       ├── pyproject.toml      # name: ops-server
│       └── src/ops_server/__init__.py
├── tests/
│   └── integration/
│       └── test_smoke.py       # 자리표시 1개 (S4에서 9칸 매트릭스가 들어올 위치)
├── docker-compose.yml          # 뼈대만: 서비스 4개 자리 선언, S2/S3/S5에서 채움
├── .github/workflows/ci.yml
└── .gitignore                  # 기존 파일에 Python 항목 추가 (.venv, __pycache__, *.db, .pytest_cache)
```

### Implementation Details

- **루트 `pyproject.toml`**: `[tool.uv.workspace] members = ["gateway", "servers/*"]`. `requires-python = ">=3.12,<3.13"` 전 패키지 동일.
- **의존성 배치**: MCP SDK·FastAPI 등 런타임 의존성은 각 패키지의 `pyproject.toml`에 (S2/S3에서 추가). 루트는 dev 의존성(pytest, ruff)만.
- **ruff**: 루트 `pyproject.toml`의 `[tool.ruff]`에 설정. `line-length = 100`, lint + format 둘 다 CI에서 검사.
- **CI** (`.github/workflows/ci.yml`): push/PR 트리거. steps: `astral-sh/setup-uv` → `uv sync` → `uv run ruff check . && uv run ruff format --check .` → `uv run pytest`.
- **docker-compose.yml**: 서비스 이름만 예약 — `gateway`(8000), `ticket-server`(8101), `docs-server`(8102), `ops-server`(8103). 이 단계에선 image/build 미지정 주석 처리로 두고, S2/S3에서 Dockerfile과 함께 채운다.
- **Karpathy 가드레일**: 요청 안 한 추상화 금지 — 공용 `common/` 패키지를 미리 만들지 않는다. 공유 코드 필요성은 S3에서 실제로 중복이 생겼을 때 판단.

## Acceptance Criteria

1. `uv sync` 가 루트에서 성공하고 `uv.lock` 이 생성된다
2. `uv run pytest` 가 통과한다 (자리표시 테스트 1개)
3. `uv run ruff check .` 와 `uv run ruff format --check .` 가 0 exit
4. 패키지 4개 각각 `uv run python -c "import gateway"` (각 패키지명) 성공
5. GitHub Actions CI가 push에서 그린
6. `.claude/` 기존 파일 변경 없음 (`git diff --stat` 으로 확인)

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Smoke | import 가능 여부 자리표시 테스트 | +1 |
| CI | lint + format + pytest 파이프라인 | 1 워크플로 |

## Rollback Plan

전부 신규 파일 — 커밋 revert로 완전 복구. 기존 파일 중 수정 대상은 `.gitignore` 뿐.

## Effort Estimate

workspace 구성 2h + CI 1h + compose 뼈대 0.5h + 검증 0.5h ≈ 0.5일 (버퍼 포함 ~1일)

## Files Reference

| File | Change |
|---|---|
| `pyproject.toml` | 신규 — workspace 루트 |
| `gateway/pyproject.toml`, `gateway/src/gateway/__init__.py` | 신규 |
| `servers/{ticket,docs,ops}/pyproject.toml` + `src/*/__init__.py` | 신규 ×3 |
| `tests/integration/test_smoke.py` | 신규 |
| `docker-compose.yml` | 신규 — 뼈대 |
| `.github/workflows/ci.yml` | 신규 |
| `.gitignore` | Python 항목 추가 |

## Out of Scope

- Dockerfile 작성 (S2/S3), 의존성 추가 (S2/S3), 공용 패키지 (필요 시 S3에서)

## Related

- Epic `00-epic.md` · 다음: S2 `02-backend-servers.md`
