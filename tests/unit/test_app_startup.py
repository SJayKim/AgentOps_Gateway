"""app.py 기동 검증 — JWT secret 없이는 build_app()이 앱을 내주지 않는다."""

import pytest

from gateway.app import build_app


@pytest.mark.parametrize("value", [None, ""])
def test_build_app_requires_jwt_secret(monkeypatch, value):
    """미설정과 빈 문자열 둘 다 기동에서 막힌다.

    conftest.py:20이 setdefault로 secret을 심어 두므로 delenv로 걷어내야 '미설정'이 재현된다.
    빈 문자열을 따로 보는 이유는 K8s Secret의 빈 값이 KeyError가 아니라 '전 토큰 invalid'라는
    조용한 실패로 나타나기 때문.
    """
    if value is None:
        monkeypatch.delenv("GATEWAY_JWT_SECRET")
    else:
        monkeypatch.setenv("GATEWAY_JWT_SECRET", value)
    with pytest.raises(RuntimeError, match="GATEWAY_JWT_SECRET"):
        build_app()
