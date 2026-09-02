"""來源是否真的從 tool 傳得回呈現層。

其他來源測試都在同一個 context 內設值再讀值，因此不管實作用 `.set()` 還是
就地改寫都會通過——而正式路徑中間隔著 LangGraph：`get_rag_answer` 由
`ToolNode` 執行，節點跑在 copy 出來的 context 裡。這支測試把那一段真的跑
一遍，是唯一擋得住「tool 內 `.set()`、外層永遠讀到空值」的防線。
"""

from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.core.rag_sources import (
    SourceRef,
    begin_request_rag_sources,
    get_request_rag_sources,
    reset_request_rag_sources,
    set_request_rag_sources,
)

_REF = SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/b")


@tool
async def _fake_rag_answer(query: str) -> str:
    """在 tool 內設定來源，模擬 RagAnswerService._append_sources。"""
    set_request_rag_sources([_REF])
    return "蜂蜜放室溫即可 [1]。"


class _State(TypedDict):
    messages: Annotated[list, add_messages]


async def _model(state: _State):
    if any(isinstance(m, ToolMessage) for m in state["messages"]):
        return {"messages": [AIMessage(content="蜂蜜放室溫即可 [1]。")]}
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "_fake_rag_answer", "args": {"query": "蜂蜜"}, "id": "1"}
                ],
            )
        ]
    }


def _route(state: _State):
    return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END


def _build_graph():
    builder = StateGraph(_State)
    builder.add_node("model", _model)
    builder.add_node("tools", ToolNode([_fake_rag_answer]))
    builder.set_entry_point("model")
    builder.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    builder.add_edge("tools", "model")
    return builder.compile()


@pytest.mark.asyncio
async def test_sources_set_inside_a_tool_reach_the_caller():
    """圖跑完後，外層必須讀得到 tool 設的來源——否則卡片不會有來源按鈕。"""
    token = begin_request_rag_sources()
    try:
        await _build_graph().ainvoke({"messages": []})

        assert get_request_rag_sources() == (_REF,)
    finally:
        reset_request_rag_sources(token)
