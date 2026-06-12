# AgentOps Gateway

여러 MCP 서버를 단일 진입점(:8000)으로 묶어 라우팅·인증·정책·감사를 담당하는
MCP Gateway. 스펙은 `docs/specs/` 참조.

## 인증 (S4)

- 토큰은 정적 사전발급: `GATEWAY_JWT_SECRET=<secret> uv run python scripts/issue_tokens.py`
- **docker-compose의 `GATEWAY_JWT_SECRET`은 데모값이다. 실제 운영 secret은
  레포에 두지 않는다** — 배포 환경의 secret 관리자에서 주입할 것.
- 정책은 `policies/policy.yaml` (default-deny), 감사 로그는 `audit/audit.jsonl`
  (append-only JSONL, 경로는 `GATEWAY_AUDIT_PATH`).

## 알려진 데모 설계 트레이드오프

- `tools/list`는 정책 필터링 없이 전체 tool을 반환한다 — support-agent가
  ops tool의 존재를 알아야 "호출 시도 → 거부" 시연이 성립하기 때문.
  production이라면 정책 기반 목록 필터링이 기본값이어야 한다.
