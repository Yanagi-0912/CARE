"""查服藥狀況直通：工具組好的文字原樣回給使用者，不再交給模型改寫。

藥名、時間、有沒有確認都是程式從資料庫組的；交回模型重寫，只是多一次把藥名
寫錯的機會，還要多等一次生成。
"""

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.services.agent.agent import (
    Agent,
    _medication_direct_reply_node,
    _route_after_tools,
)
from app.tools import medication_status_tools
from app.tools.medication_status_tools import configure_medication_status_tool

TEXT = "您今天（9/14）的用藥\n\n中 12:30　還沒確認\n• 普拿疼"


def _tool(name, content, status="success"):
    return ToolMessage(content=content, name=name, tool_call_id=f"call-{name}", status=status)


def test_routes_medication_status_straight_to_the_user():
    state = {
        "messages": [HumanMessage(content="我今天要吃什麼藥"), _tool("get_medication_status", TEXT)],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "medication_direct"


def test_direct_even_when_the_guardrail_did_not_offer_rag():
    """這個工具不隨 RAG 開關提供，guardrail 沒放行 RAG 時也要直通。"""
    state = {
        "messages": [HumanMessage(content="媽媽吃藥了嗎"), _tool("get_medication_status", TEXT)],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "medication_direct"


def test_medication_status_with_another_tool_goes_back_to_the_model():
    """只有模型能把兩份工具輸出合成一段話。"""
    state = {
        "messages": [
            HumanMessage(content="問題"),
            _tool("get_medication_status", TEXT),
            _tool("get_rag_answer", "答案"),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "agent"


def test_errored_call_is_not_sent_to_the_user():
    """參數驗證錯誤（Error invoking tool …）是給模型看的，直通就會原樣送出。"""
    error = _tool("get_medication_status", "Error invoking tool 'get_medication_status'", "error")
    state = {"messages": [HumanMessage(content="問題"), error], "allow_rag": True}
    assert _route_after_tools(state) == "agent"


def test_direct_node_returns_the_tool_text_verbatim():
    out = _medication_direct_reply_node(
        {"messages": [HumanMessage(content="問題"), _tool("get_medication_status", TEXT)]}
    )
    assert isinstance(out["messages"][0], AIMessage)
    assert out["messages"][0].content == TEXT


async def test_agent_replies_with_the_status_text_without_a_second_model_call():
    class Service:
        async def describe(self, asker_id, **kwargs):
            return TEXT

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {"name": "get_medication_status", "args": {}, "id": "c1", "type": "tool_call"}
            ],
        )
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=False)

    previous = medication_status_tools._medication_status_service
    configure_medication_status_tool(Service())
    token = set_line_user_id("U1")
    try:
        result = await Agent(llm, guardrail).invoke(user_input="我今天要吃什麼藥")
    finally:
        reset_line_user_id(token)
        configure_medication_status_tool(previous)

    assert result["response"] == TEXT
    assert llm.bind_tools.return_value.ainvoke.await_count == 1
