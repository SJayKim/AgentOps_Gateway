"""support-agent 데모 실행 — 시나리오를 stdout으로 내레이션 (수동 데모, CI 제외).

실행:
    GATEWAY_JWT_SECRET=<secret> ANTHROPIC_API_KEY=<key> uv run python -m demo_agent

env:
    GATEWAY_URL        기본 http://localhost:8000/mcp
    DEMO_AGENT_MODEL   기본 claude-sonnet-4-6 (코드 하드코딩 금지 — env로만)
"""

import asyncio
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

from demo_agent.graph import build_graph
from demo_agent.mcp_client import connect, support_token, to_anthropic_tools

USER_REQUEST = "최근 결제 오류 관련 고객 문의를 정리하고, 서버 로그에서 원인을 찾아줘"


def _narrate(message) -> None:
    role = type(message).__name__.replace("Message", "").lower()
    if getattr(message, "tool_calls", None):
        for call in message.tool_calls:
            print(f"[{role}] → tool_call {call['name']}({call['args']})")
    text = message.content if isinstance(message.content, str) else str(message.content)
    if text.strip():
        print(f"[{role}] {text}")


async def main() -> None:
    url = os.environ.get("GATEWAY_URL", "http://localhost:8000/mcp")
    model = os.environ.get("DEMO_AGENT_MODEL", "claude-sonnet-4-6")
    token = support_token(os.environ["GATEWAY_JWT_SECRET"])
    llm = ChatAnthropic(model=model, api_key=os.environ["ANTHROPIC_API_KEY"])

    async with connect(url, token) as session:
        tool_defs = to_anthropic_tools((await session.list_tools()).tools)
        graph = build_graph(session, llm, tool_defs)
        print(f"[demo] support-agent 연결됨 ({url}) · model={model}")
        print(f"[user] {USER_REQUEST}\n")
        final = await graph.ainvoke(
            {"messages": [HumanMessage(USER_REQUEST)], "denial": None, "bypass_done": False},
            config={"recursion_limit": 25},
        )
        for message in final["messages"]:
            _narrate(message)


if __name__ == "__main__":
    asyncio.run(main())
