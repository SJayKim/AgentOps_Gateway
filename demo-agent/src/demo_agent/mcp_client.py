"""Gateway에 support-agent JWT로 연결하는 MCP 클라이언트 + tool 포맷 변환.

토큰은 여기서 직접 발급한다 (발급 서버 없음 — scripts/issue_tokens.py와 동일 claim).
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def support_token(secret: str, hours: int = 1) -> str:
    """support-agent용 단명 HS256 토큰 — claim은 {agent_id, exp}뿐."""
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    return jwt.encode({"agent_id": "support-agent", "exp": exp}, secret, algorithm="HS256")


@asynccontextmanager
async def connect(url: str, token: str):
    """초기화까지 마친 MCP ClientSession을 yield."""
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def to_anthropic_tools(mcp_tools) -> list[dict]:
    """MCP Tool 목록 → Claude bind_tools가 받는 {name, description, input_schema} 형식."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in mcp_tools
    ]
