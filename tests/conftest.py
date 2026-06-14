"""전 테스트 공통 셋업 — import 경로·기본 환경변수를 한 곳에서 잡는다.

[왜 conftest.py인가]
pytest가 테스트 수집 전에 이 파일을 자동 로드한다. 그래서 "모든 테스트가 시작되기 전에
딱 한 번" 해둬야 하는 일(경로 등록, 기본 env)을 여기 둔다. 각 테스트가 같은 보일러플레이트를
반복하지 않게 하는 게 목적이다.
"""

import os
import sys
import tempfile
from pathlib import Path

# scripts/를 import 경로에 추가 — 테스트가 issue_tokens.issue_token을 그대로 재사용해
# "발급=검증"이 한 함수에서 정의되게 한다(AC7). 별도 토큰 픽스처를 만들지 않는 이유.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# 검증 측(gateway.auth)과 발급 측이 공유할 JWT secret. setdefault라 외부에서 이미 지정했으면
# 존중한다 — 테스트는 고정 secret이면 충분(보안이 아니라 결정성이 목적).
os.environ.setdefault("GATEWAY_JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
# 기본 audit 경로를 임시 디렉터리로 돌려 레포의 audit/를 오염시키지 않게 한다. audit 내용을
# 단언하는 개별 테스트는 자기만의 tmp 경로로 다시 덮어쓴다(여긴 '안전한 기본값'일 뿐).
os.environ.setdefault("GATEWAY_AUDIT_PATH", str(Path(tempfile.mkdtemp()) / "audit.jsonl"))
