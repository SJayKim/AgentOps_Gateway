"""JWT 인증 — 정적 사전발급 토큰을 검증해 agent_id를 얻는다.

[설계 — 왜 이렇게 단순한가]
이 데모는 토큰 "발급" 서버를 만들지 않는다. 셋업 스크립트(scripts/issue_tokens.py)가
에이전트 3개분 토큰을 미리 찍어 두고, Gateway는 그걸 검증만 한다. 발급 인프라(로그인,
회전, 발급 API)는 프로젝트의 핵심 가치(권한 매트릭스 enforcement)와 무관하므로 일부러
범위에서 뺐다 (design.md "구현 명세 결정사항": 정적 사전발급, HS256, secret은 env var).

[실패 사유를 3종으로 고정하는 이유]
reason은 missing | invalid | expired 셋으로만 고정한다. 이 문자열이 AUTH_FAILED
payload의 reason 필드 계약이고, S6 에이전트/테스트가 이 값을 보고 분기한다. 그래서
임의 메시지가 아니라 안정적인 enum이어야 한다.
"""

import os

import jwt


class AuthError(Exception):
    """인증 실패. reason에 안정적 사유 코드를 담아 호출처(app.py)가 응답으로 변환한다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # missing | invalid | expired — AUTH_FAILED payload 계약


def authenticate(authorization: str | None) -> str:
    """Authorization 헤더를 검증해 agent_id 문자열을 반환. 실패 시 AuthError(reason)."""
    # "Bearer <token>" 형식이 아니면 토큰 자체가 없는 것으로 본다.
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing")
    token = authorization.removeprefix("Bearer ")
    try:
        # HS256 대칭키 검증. secret은 env에서 직접 읽는다(미설정이면 KeyError로 터지게 둠 —
        # secret 없이 가동되는 건 설정 사고이므로 조용히 통과시키지 않는다).
        # decode는 서명 검증 + exp 만료 검사를 함께 수행한다.
        claims = jwt.decode(token, os.environ["GATEWAY_JWT_SECRET"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        # exp claim은 지났지만 서명은 유효 — "만료"로 구분해 돌려준다(우회 불가 사유).
        raise AuthError("expired") from None
    except jwt.InvalidTokenError:
        # 서명 불일치·형식 오류 등 그 외 모든 토큰 문제는 "invalid"로 묶는다.
        raise AuthError("invalid") from None
    agent_id = claims.get("agent_id")
    if not agent_id:
        # 서명은 유효하지만 우리 계약(agent_id claim)을 안 따르는 토큰 → invalid 취급.
        raise AuthError("invalid")
    return agent_id
