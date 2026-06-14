"""에이전트 3개분 정적 JWT를 생성해 stdout으로 출력 — 발급 서버는 만들지 않는다.

[왜 스크립트 한 장인가 — design.md 구현 명세]
데모는 토큰 '발급 인프라'(로그인·회전·발급 API)를 만들지 않는다. 그건 프로젝트의 핵심
가치(권한 매트릭스 enforcement)와 무관한 곁가지다. 대신 이 스크립트가 3개 토큰을 미리
찍어 주고, 사용자는 그걸 복사해 Authorization 헤더에 쓴다. 테스트 픽스처도 issue_token을
그대로 재사용해 "발급 = 검증"의 짝이 한 곳에서 정의되게 했다.

실행: GATEWAY_JWT_SECRET=<secret> uv run python scripts/issue_tokens.py
"""

import os
from datetime import datetime, timedelta, timezone

import jwt

# 권한 매트릭스의 3 에이전트 — 이 agent_id가 곧 정책 YAML의 키이자 audit/메트릭의 라벨.
AGENTS = ["support-agent", "analyst-agent", "dev-agent"]


def issue_token(agent_id: str, secret: str, days: int = 30) -> str:
    """HS256 토큰을 만든다. claim은 {"agent_id", "exp"} 둘 뿐 — Gateway auth가 보는 게 그게 전부다.

    만료 30일은 순전히 데모 편의(매번 재발급하지 않아도 되게). 운영이라면 짧은 수명 +
    회전이 맞지만, 그건 이 데모의 범위 밖이다.
    """
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    return jwt.encode({"agent_id": agent_id, "exp": exp}, secret, algorithm="HS256")


if __name__ == "__main__":
    # secret은 검증 측(gateway.auth)과 반드시 같아야 한다 — 같은 env var를 공유하는 이유.
    secret = os.environ["GATEWAY_JWT_SECRET"]
    for agent in AGENTS:
        print(f"{agent}: {issue_token(agent, secret)}")
