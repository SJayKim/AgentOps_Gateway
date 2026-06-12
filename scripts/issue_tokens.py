"""에이전트 3개분 정적 JWT를 생성해 stdout으로 출력 — 발급 서버는 만들지 않는다.

데모 사용자가 복사해 쓰는 형태. 테스트 픽스처도 issue_token을 그대로 재사용한다.
실행: GATEWAY_JWT_SECRET=<secret> uv run python scripts/issue_tokens.py
"""

import os
from datetime import datetime, timedelta, timezone

import jwt

AGENTS = ["support-agent", "analyst-agent", "dev-agent"]


def issue_token(agent_id: str, secret: str, days: int = 30) -> str:
    """HS256 토큰 — claim은 {"agent_id", "exp"} 뿐. 만료 30일은 데모 편의."""
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    return jwt.encode({"agent_id": agent_id, "exp": exp}, secret, algorithm="HS256")


if __name__ == "__main__":
    secret = os.environ["GATEWAY_JWT_SECRET"]
    for agent in AGENTS:
        print(f"{agent}: {issue_token(agent, secret)}")
