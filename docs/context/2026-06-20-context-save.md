---
status: in-progress
branch: main
timestamp: 2026-06-20T12:56:06+09:00
files_modified:
  - scripts/check_gateway.py (커밋됨 61ad9d9 — dev-agent 토큰 추가)
  - docs/design/agentops-gateway-design.md (커밋됨 03d2c6b — Wedge Kill Criterion)
  - scripts/specs_to_issues.sh (신규, 커밋됨 03d2c6b — specs 이슈화 스크립트)
  - TODOS.md (커밋됨 03d2c6b — 2건 상태 갱신)
---

## Working on: AgentOps Gateway — check_gateway 실측 검증 + kill criterion + specs 이슈화 준비

### Summary

핵심 데모 시스템(S1–S6 + S5 stretch)은 안정 상태. 이번 세션은 06-20 초반 체크포인트의
잔여 작업을 마감했다. `check_gateway.py` 수정을 라이브 게이트웨이로 실측 통과시켰고,
TODOS.md 2건 중 #1(kill criterion)을 완료, #2(specs 이슈화)는 스크립트 핸드오프까지.
모든 작업물 커밋·푸시 완료 (working tree clean). 남은 일은 코드 밖 The Assignment와
gh 인증 후 1커맨드뿐이다.

### Done this session (2026-06-20)

- **`check_gateway.py` 라이브 실측 통과** (커밋 `61ad9d9`, 본 세션에서 검증):
  - `docker compose up -d --wait`(6 서비스 healthy) 후
    `GATEWAY_JWT_SECRET=demo-secret-do-not-use-in-prod uv run python scripts/check_gateway.py`.
  - **AC1 OK**: 7개 prefixed tool 집계. **AC2 OK**: `ticket__create_ticket` → `{id:1, status:open}`
    (라우팅+쓰기). **AC3 OK**: docs+ops 게이트웨이 경유 정상.
  - 06-15 finding이던 "미인증 initialize가 이중 계층 auth(`app.py:233`) 401에 막힘"이
    dev-agent 토큰으로 실제 해소됨을 라이브로 확인. 실측 후 `docker compose down`.
  - (출력된 InsecureKeyLengthWarning는 데모 secret 30바이트 < 권장 32바이트 탓, 무해.)

- **Wedge Kill Criterion 작성** (커밋 `03d2c6b`, TODOS #1 완료):
  - 위치: `docs/design/agentops-gateway-design.md` "Target User & Narrowest Wedge"의
    `### Wedge Kill Criterion` — wedge 결정이 사는 구역.
  - 3기준: **중단**(이미 상용/OSS gateway로 만족 중) / **재검토 착수**(수동 크리덴셜 +
    감사 없음) / **기본값**(모호 시 wedge 보류, 1건 더 확보 후 재판단).
  - 적용범위는 스타트업 wedge에만 한정 — 포트폴리오 가치(관측성·커리어 축)는 kill 대상 아님.
  - 2026-06-11 eng review의 "대화 나쁠 때 출구 없음" 지적을 닫음.

- **specs 이슈화 스크립트 준비** (커밋 `03d2c6b`, TODOS #2 핸드오프):
  - `scripts/specs_to_issues.sh` — Epic+S1–S6 이슈 7개 생성 + 각 spec 상단 blockquote
    메타에 `> 이슈: #N` 역링크. 멱등(이미 링크된 spec 건너뜀).
  - specs는 YAML frontmatter가 없어 상단 `> ` 메타 줄에 링크하도록 설계.

### Decisions Made

- check_gateway는 '집계·중계 경로' 확인 도구 — 토큰은 거부를 보려는 게 아니라 401 enforcement
  통과용. 정책 거부 시나리오는 계속 `e2e_demo.py`(support-agent) 담당, 역할 분리 유지.
- specs 이슈 생성은 외부로 나가는 동작 + gh 인증 수동이라, 스크립트만 준비하고 트리거는
  사용자가 직접. (`gh` 이 환경 미설치.)

### Remaining Work

1. **specs → GitHub 이슈 (#2, 미완)** — gh 인증 후 1커맨드: `winget install --id GitHub.cli`
   → `gh auth login`(브라우저) → `bash scripts/specs_to_issues.sh`.
2. **The Assignment** — 첫 대상 회사 대화 (코드 밖). 이제 kill criterion이 있어 결과 해석
   기준 존재. S6 시나리오를 실제 상황으로 교체하는 유일한 입력.
3. 코드 관련 잔여 작업은 비어 있음.

### Notes

- 이번 세션 커밋: `03d2c6b docs: wedge kill criterion 작성 + specs 이슈화 스크립트 준비`,
  직전 `61ad9d9 fix: check_gateway.py에 dev-agent 토큰 추가`. 둘 다 origin/main 푸시됨.
- `scripts/specs_to_issues.sh`는 Git Bash(`bash …`)로 실행. LF→CRLF 경고는 Windows 정규화라 무해.
- gstack 체크포인트 미러: `~/.gstack/.../checkpoints/20260620-125606-kill-criterion-specs-issue-prep.md`.
