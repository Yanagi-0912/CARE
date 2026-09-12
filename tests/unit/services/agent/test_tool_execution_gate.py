"""執行層把關：只執行本輪有綁給模型的工具。

2026-09-10 線上日誌記下的繞過路徑：guardrail 判 allow_rag=False，RAG 工具沒有
綁給模型，但模型仍輸出 get_rag_answer 呼叫（先猜錯參數名 question，被驗證錯誤
教會改成 query 後重試成功），而用全部工具建的 ToolNode 照跑。
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.agent.agent import _BLOCKED_TOOL_REPLY, _execute_offered_tools


class _FakeExecutor:
    """記下被交付的 state，並對最後一則 AIMessage 的每個呼叫回一則成功訊息。"""

    def __init__(self):
        self.calls = []

    async def ainvoke(self, state, config=None):
        self.calls.append(state)
        last = state["messages"][-1]
        return {
            "messages": [
                ToolMessage(content=f"ran {tc['name']}", name=tc["name"], tool_call_id=tc["id"])
                for tc in last.tool_calls
            ]
        }


def _ai_calls(*calls) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ],
    )


@pytest.mark.asyncio
async def test_unoffered_rag_call_is_blocked_not_executed():
    """事件原樣重現：allow_rag=False、模型以錯誤參數名呼叫 RAG。"""
    executor = _FakeExecutor()
    state = {
        "messages": [HumanMessage(content="法國國歌"), _ai_calls(("get_rag_answer", {"question": "法國國歌"}))],
        "allow_rag": False,
    }

    out = await _execute_offered_tools(state, None, executor)

    assert executor.calls == []
    [reply] = out["messages"]
    assert reply.status == "error"
    assert reply.name == "get_rag_answer"
    assert reply.tool_call_id == "call-0"


@pytest.mark.asyncio
async def test_blocked_reply_does_not_teach_the_tool_signature():
    """
    事件裡是 ToolNode 的驗證錯誤（「query: Field required」）把正確參數名教給了
    模型。拒絕訊息不能重蹈覆轍。
    """
    assert "query" not in _BLOCKED_TOOL_REPLY
    assert "get_rag_answer" not in _BLOCKED_TOOL_REPLY


@pytest.mark.asyncio
async def test_offered_rag_call_executes_unchanged():
    """allow_rag=True 時行為與導入前相同：原 state 原封不動交給 ToolNode。"""
    executor = _FakeExecutor()
    state = {
        "messages": [HumanMessage(content="高血壓"), _ai_calls(("get_rag_answer", {"query": "高血壓"}))],
        "allow_rag": True,
    }

    out = await _execute_offered_tools(state, None, executor)

    assert executor.calls == [state]
    assert [m.content for m in out["messages"]] == ["ran get_rag_answer"]


@pytest.mark.asyncio
async def test_mixed_calls_execute_only_offered_ones_in_original_order():
    executor = _FakeExecutor()
    state = {
        "messages": [
            HumanMessage(content="問題"),
            _ai_calls(("get_rag_answer", {"query": "x"}), ("find_nearby_hospitals", {"lat": 25.0, "lng": 121.5})),
        ],
        "allow_rag": False,
    }

    out = await _execute_offered_tools(state, None, executor)

    [executed_state] = executor.calls
    assert [tc["name"] for tc in executed_state["messages"][-1].tool_calls] == ["find_nearby_hospitals"]
    assert [(m.tool_call_id, m.status) for m in out["messages"]] == [("call-0", "error"), ("call-1", "success")]


@pytest.mark.asyncio
async def test_base_tools_still_run_when_rag_is_not_offered():
    """強制呼叫的院所工具屬於基本工具，guardrail 判 False 時也必須照跑。"""
    executor = _FakeExecutor()
    state = {
        "messages": [HumanMessage(content="附近醫院"), _ai_calls(("find_nearby_hospitals", {"lat": 25.0, "lng": 121.5}))],
        "allow_rag": False,
    }

    out = await _execute_offered_tools(state, None, executor)

    assert executor.calls == [state]
    assert out["messages"][0].content == "ran find_nearby_hospitals"


@pytest.mark.asyncio
async def test_blocked_call_is_observable(monkeypatch):
    stages = []
    monkeypatch.setattr(
        "app.services.agent.agent.log_stage",
        lambda _logger, stage, **fields: stages.append((stage, fields)),
    )
    state = {
        "messages": [HumanMessage(content="軍艦進行曲"), _ai_calls(("get_rag_answer", {"question": "軍艦進行曲"}))],
        "allow_rag": False,
    }

    await _execute_offered_tools(state, None, _FakeExecutor())

    blocked = [fields for stage, fields in stages if stage == "tool_blocked"]
    assert blocked == [{"names": ["get_rag_answer"], "allow_rag": False}]
