# 공통 패턴 ×3 서버 — 워크스페이스 루트를 컨텍스트로 빌드, 패키지별 빌드 인자
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ARG PACKAGE=docs-server
ARG MODULE=docs_server
WORKDIR /app
COPY . .
# TLS 검사 소프트웨어가 있는 머신 대응 — certs/*.crt 있으면 신뢰 저장소에 추가 (없으면 no-op)
RUN cat certs/*.crt >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true
RUN UV_NATIVE_TLS=1 UV_SYSTEM_CERTS=1 uv sync --frozen --no-dev --package $PACKAGE
ENV MODULE=$MODULE
CMD uv run --no-sync python -m $MODULE
