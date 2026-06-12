"""테스트 공통 — scripts/issue_tokens.py를 픽스처로 재사용 (AC7) + 기본 env."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("GATEWAY_JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
# 기본 audit 경로가 레포의 audit/를 오염시키지 않도록 — 개별 테스트가 덮어쓴다
os.environ.setdefault("GATEWAY_AUDIT_PATH", str(Path(tempfile.mkdtemp()) / "audit.jsonl"))
