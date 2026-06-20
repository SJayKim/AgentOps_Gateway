---
status: in-progress
branch: main
timestamp: 2026-06-20T12:23:00+09:00
files_modified:
  - scripts/check_gateway.py (미커밋 — stale auth 가정 수정)
  - docs/context/2026-06-20-context-save.md (신규 — 본 상태 기록)
---

## Working on: AgentOps Gateway — 데모 안정 상태 유지, check_gateway.py stale 수정

### Summary

2026-06-15 context-save 이후 코드 변경은 한 건. 그때 "Remaining Work" 3번(선택)으로 남겼던
`scripts/check_gateway.py` stale 정리를 이번 세션에 마감했다. 핵심 데모 시스템(S1–S6 + S5
stretch)은 여전히 안정 상태이고, 97 테스트 그린·docker compose e2e는 06-15 시점 그대로 유효.
남은 일은 전부 코드 밖(The Assignment, TODOS 2건)이다.

### Done this session (2026-06-20)

- **`scripts/check_gateway.py` stale 수정** (미커밋, working tree에만 존재):
  - 근본 원인은 docstring만 낡은 게 아니라 **스크립트가 동작 불가** 상태였던 것. 스크립트는
    `session.initialize()`를 먼저 호출하는데, gateway의 이중 계층 auth(`app.py:233`)가 미인증
    `initialize`/`tools-list`를 HTTP 401로 끊는다. 토큰 없이는 첫 줄에서 죽는다.
  - 그래서 옵션 ②(docstring만 정정)는 불충분 — 옵션 ①(토큰 추가)이 실제 수정.
  - `from issue_tokens import issue_token` 추가(`e2e_demo.py`와 동일 패턴, 같은 `scripts/`라
    import 경로 동일), `AGENT = "dev-agent"`, `main()`에서 토큰 발급 → `Authorization: Bearer`
    헤더 주입, docstring을 현재 동작 반영으로 재작성.
  - **`dev-agent` 선택 근거**: AC2가 `ticket__create_ticket`, AC3가 `docs__search_docs` +
    `ops__get_metrics`를 호출하는데, `policy.yaml`상 셋을 전부 허용받는 유일한 에이전트가
    dev-agent다(support-agent는 ops 없음, analyst-agent는 create_ticket 불가). 그래야 어떤
    호출도 정책 거부에 걸려 '집계·중계 경로' 검증을 오염시키지 않는다.
  - 검증: `py_compile` 통과. 토큰/헤더 로직은 이미 e2e 실측된 `e2e_demo.py`와 동일하고,
    에이전트 선택은 `policy.yaml:14-17`에 대조 확인. 라이브 풀스택 실행은 이 한 스크립트
    때문에 띄우기 과해서 생략.

### Decisions Made

- check_gateway는 '정책 거부'가 아니라 '집계·중계 경로' 확인 도구라는 원래 의도를 유지.
  토큰은 거부를 보려는 게 아니라 401 enforcement를 통과하기 위한 것. 정책 거부 시나리오는
  계속 `e2e_demo.py`(support-agent)가 맡는다.

### Remaining Work

1. **The Assignment** — 첫 대상 회사 대화. S6 시나리오를 실제 상황으로 교체하는 유일한 입력.
2. **TODOS.md 2건 (코드 외, 솔로라 선택)**: ① The Assignment kill criterion 1–3줄 작성
   ② specs를 GitHub 이슈로 등록 (`gh auth login` 후).
3. **(선택) check_gateway.py 변경 커밋** — 현재 working tree에만 있음. 라이브 게이트웨이로
   실측까지 하려면: `docker compose up` 후
   `GATEWAY_JWT_SECRET=<secret> uv run python scripts/check_gateway.py`.

### Notes

- 06-15 시점 findings(check_gateway stale)는 본 세션에서 해소됨. 그 외 미해결 findings 없음.
- 이 변경은 아직 커밋 안 됨 — `git status`에 ` M scripts/check_gateway.py`로 남아 있다.
