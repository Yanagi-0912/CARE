"""家庭名單工具的固定文字不經第二次模型改寫。"""

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.services.agent.agent import (
    Agent,
    _family_directory_direct_reply_node,
    _route_after_tools,
)
from app.tools import family_directory_tools
from app.tools.family_directory_tools import configure_family_directory_tool

TEXT = "您設定為父／母的家人：王美玲、陳大明。"


def _tool(name, content, status="success"):
    return ToolMessage(
        content=content,
        name=name,
        tool_call_id=f"call-{name}",
        status=status,
    )


def test_routes_a_successful_family_query_straight_to_the_user():
    state = {
        "messages": [HumanMessage(content="我的父母是誰"), _tool("get_family_directory", TEXT)],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "family_directory_direct"


def test_family_query_with_another_tool_returns_to_the_model():
    state = {
        "messages": [
            HumanMessage(content="問題"),
            _tool("get_family_directory", TEXT),
            _tool("get_medication_status", "用藥資料"),
        ],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "agent"


def test_errored_family_query_is_not_exposed_to_the_user():
    state = {
        "messages": [
            HumanMessage(content="問題"),
            _tool("get_family_directory", "Error invoking tool", "error"),
        ],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "agent"


def test_direct_node_returns_the_directory_text_verbatim():
    result = _family_directory_direct_reply_node(
        {"messages": [HumanMessage(content="問題"), _tool("get_family_directory", TEXT)]}
    )
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == TEXT


async def test_agent_does_not_call_the_model_again_after_the_directory_tool():
    class Service:
        async def describe(self, operator_id, **kwargs):
            return TEXT

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_family_directory",
                    "args": {"relationship": "parent"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        )
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=False)

    previous = family_directory_tools._family_directory_service
    configure_family_directory_tool(Service())
    token = set_line_user_id("U_ME")
    try:
        result = await Agent(llm, guardrail).invoke(user_input="我的父母是誰")
    finally:
        reset_line_user_id(token)
        configure_family_directory_tool(previous)

    assert result["response"] == TEXT
    assert llm.bind_tools.return_value.ainvoke.await_count == 1
