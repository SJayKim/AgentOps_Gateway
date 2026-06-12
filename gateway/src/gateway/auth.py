"""JWT 인증 — 정적 사전발급 토큰 검증 (발급 서버 없음, scripts/issue_tokens.py 참조).

HS256, secret은 env GATEWAY_JWT_SECRET. 실패 reason은 missing|invalid|expired
3종으로 고정 — AUTH_FAILED payload의 reason 필드 계약 (S6 파싱 대상).
"""

import os

import jwt


class AuthError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # missing | invalid | expired


def authenticate(authorization: str | None) -> str:
    """Authorization 헤더 검증 → agent_id. 실패 시 AuthError(reason)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing")
    token = authorization.removeprefix("Bearer ")
    try:
        claims = jwt.decode(token, os.environ["GATEWAY_JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthError("expired") from None
    except jwt.InvalidTokenError:
        raise AuthError("invalid") from None
    agent_id = claims.get("agent_id")
    if not agent_id:
        raise AuthError("invalid")
    return agent_id
