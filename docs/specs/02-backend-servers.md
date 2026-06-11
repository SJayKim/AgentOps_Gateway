# S2: 백엔드 MCP 서버 3종 — ticket / docs / ops

> Epic: `00-epic.md` · 의존: S1 · Effort: ~2-3일

## Context

Gateway(S3)가 라우팅할 대상이자 권한 매트릭스(S4)의 열(column)이 되는 백엔드 3종. 외부 의존성 없는 완전 통제 시뮬레이션 환경이 설계 제약이므로, 셋 다 직접 구현한다. 각 서버는 성격이 다르다: ticket은 쓰기 있는 정책 테스트용, docs는 읽기 전용 대표, ops는 "민감 데이터" 역할.

## Current State

S1 완료 시점 기준 `servers/{ticket,docs,ops}/` 에 빈 패키지만 존재. MCP 서버 코드 없음.

## Proposed Change

서버 3종을 각각 MCP Python SDK로 구현하고 Streamable HTTP로 단독 서빙한다. **tool 시그니처는 아래에 고정한다** — 특히 `query_logs`의 시그니처는 S4 인자 레벨 정책의 전제이므로 변경 금지.

### Tool 명세 (고정)

**ticket-server (:8101)** — SQLite (`tickets.db`, 컨테이너 로컬)

```
create_ticket(title: str, body: str) -> {"id": int, "status": "open"}
search_tickets(query: str) -> [{"id": int, "title": str, "status": str}]   # title/body LIKE 검색
update_status(ticket_id: int, status: str) -> {"id": int, "status": str}   # status ∈ {open, in_progress, closed}
```

스키마:
```sql
CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL  -- ISO8601
);
```

**docs-server (:8102)** — 마크다운 저장소 + BM25 검색 (`rank_bm25` 라이브러리, 시드 문서 ~10개를 `servers/docs/corpus/*.md`로 동봉)

```
search_docs(query: str) -> [{"doc_id": str, "score": float, "snippet": str}]  # 상위 5건
read_doc(doc_id: str) -> {"doc_id": str, "content": str}                      # 미존재 시 isError
```

**ops-server (:8103)** — 가짜 운영 데이터 (결정적 생성기, 시드 고정 — 테스트 재현성)

```
get_metrics(metric: str) -> {"metric": str, "points": [{"ts": ISO8601, "value": float}]}  # metric ∈ {cpu, memory, requests}
query_logs(query: str, start: ISO8601, end: ISO8601) -> {"lines": [str], "count": int}
```

`query_logs` 는 시그니처 자체가 S4 정책 검사(`end - start <= 24h`)의 대상이다. 서버 자체는 범위 제한을 하지 않는다 — 제한은 Gateway 정책의 몫 (관심사 분리, 그리고 "Gateway가 없으면 강제 불가"라는 존재 이유의 증명).

### Implementation Details

- 각 서버는 MCP Python SDK의 server API + Streamable HTTP transport로 단독 실행: `uv run python -m ticket_server` 형태의 `__main__.py` 엔트리포인트.
- 잘못된 입력(미존재 doc_id, 잘못된 status 값, 파싱 불가 ISO8601)은 MCP tool result `isError: true` + 사람이 읽을 메시지로 응답. 서버 크래시 금지.
- 의존성은 각 패키지 `pyproject.toml`에: 공통 `mcp`, ticket은 표준 `sqlite3`, docs는 `rank_bm25`.
- Dockerfile 1개 패턴을 3서버에 적용 (`uv` 베이스, 패키지별 빌드 인자). `docker-compose.yml`의 해당 서비스 3개를 채운다.
- 데이터는 전부 컨테이너/로컬 임시 — 볼륨 마운트 없음 (데모 재기동 = 깨끗한 상태).

## Acceptance Criteria

1. 서버 3종이 각각 단독 기동되고, MCP 클라이언트로 `tools/list` 호출 시 위 명세의 tool 7개가 (서버별 3/2/2) 정확한 이름·파라미터 스키마로 반환된다
2. ticket: `create_ticket` → `search_tickets`(생성한 제목으로 검색 적중) → `update_status` 라운드트립 통과
3. docs: 시드 문서에 존재하는 단어로 `search_docs` 호출 시 1건 이상 반환, `read_doc`은 그 doc_id의 전문 반환, 미존재 doc_id는 `isError: true`
4. ops: `get_metrics("cpu")` 가 포인트 1개 이상 반환, `query_logs` 가 24시간 초과 범위 요청도 **정상 처리** (서버는 제한하지 않음을 테스트로 명시)
5. 잘못된 입력 3종(미존재 doc_id, 무효 status, 무효 ISO8601)이 전부 `isError: true` + 비크래시
6. `docker compose up ticket-server docs-server ops-server` 로 3종 동시 기동
7. lint + 기존 테스트 그린 유지

## Testing Plan

| Layer | What | Count |
|---|---|---|
| Unit | 서버별 tool 핸들러 (정상 + 오류 입력) | +9 (서버당 3) |
| Integration | MCP 클라이언트 → 각 서버 라운드트립 (AC 1-4) | +4 |

## Rollback Plan

신규 코드만 — revert로 복구. SQLite 파일은 로컬 생성물이라 삭제로 충분.

## Effort Estimate

ticket 0.5일 + docs(BM25 포함) 0.5일 + ops 0.5일 + Docker화·통합테스트 0.5일 ≈ 2일 (버퍼 ~3일)

## Files Reference

| File | Change |
|---|---|
| `servers/ticket/src/ticket_server/{__main__,server,db}.py` | 신규 |
| `servers/docs/src/docs_server/{__main__,server,search}.py` + `corpus/*.md` | 신규 |
| `servers/ops/src/ops_server/{__main__,server,fake_data}.py` | 신규 |
| `servers/*/Dockerfile` | 신규 ×3 |
| `docker-compose.yml` | 서비스 3개 채움 |
| `tests/integration/test_backend_servers.py` | 신규 |

## Out of Scope

- 인증·정책 (서버는 누가 호출하든 응답한다 — 통제는 전부 Gateway = S4)
- docs corpus의 실제 콘텐츠 품질 (검색 데모가 되는 수준이면 충분)
- ticket DB 영속화 / 마이그레이션

## Related

- Epic `00-epic.md` · 선행: S1 · 다음: S3 `03-gateway-core.md`
