#!/usr/bin/env bash
# docs/specs/ 의 Epic + S1-S6 을 GitHub 이슈로 등록하고, 각 spec 상단 메타
# blockquote 에 이슈 번호를 역링크한다 (/ship 의 이슈 auto-close 연동을 살림).
#
# 전제: gh 설치 + `gh auth login` 완료. origin 리모트가 대상 레포.
# 멱등: 이미 `> 이슈:` 링크가 있는 spec 은 건너뛴다 — 두 번 돌려도 중복 생성 안 됨.
# 실행: bash scripts/specs_to_issues.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# 등록 순서 — epic 먼저(자식이 참조할 부모), 그다음 S1..S6.
SPECS=(
  docs/specs/00-epic.md
  docs/specs/01-scaffold.md
  docs/specs/02-backend-servers.md
  docs/specs/03-gateway-core.md
  docs/specs/04-auth-policy-audit.md
  docs/specs/05-observability.md
  docs/specs/06-langgraph-admin.md
)

for f in "${SPECS[@]}"; do
  if grep -q '^> 이슈:' "$f"; then
    echo "skip (이미 링크됨): $f"
    continue
  fi
  title=$(grep -m1 '^# ' "$f" | sed 's/^# //')
  url=$(gh issue create --title "$title" --body-file "$f")  # 본문 = spec 전문
  num="${url##*/}"
  echo "created #$num: $title"
  # 첫 blockquote(>) 메타 줄 바로 뒤에 역링크 한 줄 삽입.
  awk -v link="> 이슈: #$num — $url" '
    !ins && /^> / { print; print link; ins=1; next }
    { print }
  ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done

echo "완료. 'git diff docs/specs/' 로 역링크 확인 후 커밋하라."
