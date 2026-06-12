"""auth.py 단위 — 인증 3분기(missing/invalid/expired) + 정상 토큰."""

import os

import pytest
from issue_tokens import issue_token

from gateway import auth

SECRET = os.environ["GATEWAY_JWT_SECRET"]


def test_valid_token_returns_agent_id():
    token = issue_token("support-agent", SECRET)
    assert auth.authenticate(f"Bearer {token}") == "support-agent"


def test_missing_header():
    for header in (None, "", "Basic abc"):
        with pytest.raises(auth.AuthError) as e:
            auth.authenticate(header)
        assert e.value.reason == "missing"


def test_invalid_signature():
    token = issue_token("support-agent", "wrong-secret-0123456789abcdef0123456789abcdef")
    with pytest.raises(auth.AuthError) as e:
        auth.authenticate(f"Bearer {token}")
    assert e.value.reason == "invalid"


def test_expired_token():
    token = issue_token("support-agent", SECRET, days=-1)
    with pytest.raises(auth.AuthError) as e:
        auth.authenticate(f"Bearer {token}")
    assert e.value.reason == "expired"
