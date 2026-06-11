# TODOS

## The Assignment kill criterion 정의
- **What:** 첫 대상 회사 대화 전에, 어떤 답이 나오면 "S6 시나리오 교체"가 아니라 "wedge 추구 재검토/중단"인지 기준을 1-3줄로 적어둔다.
- **Why:** 현재 계획은 대화 결과가 나쁠 때의 출구가 없다 (2026-06-11 eng review, Codex 외부 목소리 지적). 기준 없이 대화하면 결과 해석이 확증 편향에 노출된다.
- **Context:** 설계 문서(docs/design/agentops-gateway-design.md)는 Demand Evidence를 "현재 0, 정직하게 기록"으로 두고 있다. 포트폴리오 가치(관측성 스택, 커리어 축)는 kill 대상이 아니다 — 기준은 스타트업 wedge(Approach C 요소) 추구 여부에만 적용하면 된다. 예시 기준: "이미 상용 MCP gateway를 도입해 만족 중"이라는 답이면 wedge 탐색 중단, "수동 크리덴셜 관리 + 감사 없음"이면 wedge 재검토 착수.
- **Depends on:** The Assignment 실행 전에 작성 (S1-S3과 병행).

## specs를 GitHub 이슈로 등록
- **What:** `gh auth login` 후 docs/specs/의 Epic + S1-S6를 GitHub 이슈로 올리고, 각 spec frontmatter에 이슈 번호를 연결한다.
- **Why:** /ship의 이슈 auto-close 연동이 살아나고, 공개 레포에서 작업 이력이 이슈 타임라인으로 보인다 (포트폴리오 신호).
- **Context:** 2026-06-11 spec 작성 시점에 gh 미인증이라 파일로만 저장했다 (체크포인트 기록). 솔로 프로젝트라 필수 아님 — 파일 기반으로도 워크플로우는 완전 동작. 레포 공개(Distribution Plan) 전에 하면 충분.
- **Depends on:** gh auth login (수동). S1 착수보다 우선순위 낮음.
