"""
agent ↔ tools 迴圈的上限：從 graph 入口打進去，用真的 LangGraph 計步。

模型每一步都呼叫本輪沒提供的工具（2026-09-10 事件的形態）時，
`_execute_offered_tools` 會一路攔下，停下來的只有 recursion_limit。
不明確傳入時 LangGraph 1.1.10 的預設是 10007，等於沒有上限。
"""

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from app.services.agent.agent import MAX_TOOL_ROUNDS, Agent


def _blocked_call(n: int) -> AIMessage:
    # allow_rag=False 時 get_rag_answer 不在提供的工具內，執行層會攔下，
    # 不會真的跑 RAG。
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_rag_answer",
                "args": {"query": "法國國歌"},
                "id": f"blocked_{n}",
                "type": "tool_call",
            }
        ],
    )


class _ScriptedLLM:
    """前 *tool_rounds* 次都呼叫被攔的工具，之後回純文字。"""

    def __init__(self, tool_rounds: int):
        self.tool_rounds = tool_rounds
        self.invocations = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        self.invocations += 1
        if self.invocations <= self.tool_rounds:
            return _blocked_call(self.invocations)
        return AIMessage(content="最終回覆")


class _FakeGuardrail:
    async def allow_rag_tool(self, _text):
        return False


def _agent(llm) -> Agent:
    return Agent(llm=llm, guardrail_service=_FakeGuardrail())


@pytest.mark.asyncio
async def test_longest_legitimate_path_completes():
    """最長合法路徑（MAX_TOOL_ROUNDS 次往返後回話）必須能正常結束。"""
    llm = _ScriptedLLM(tool_rounds=MAX_TOOL_ROUNDS)

    result = await _agent(llm).invoke(user_input="你好")

    assert result["response"] == "最終回覆"
    assert llm.invocations == MAX_TOOL_ROUNDS + 1


@pytest.mark.asyncio
async def test_one_round_over_the_limit_is_stopped():
    """上限是緊的：多一次往返就中止，不是靠很大的預設值剛好沒撞到。"""
    llm = _ScriptedLLM(tool_rounds=MAX_TOOL_ROUNDS + 1)

    with pytest.raises(GraphRecursionError):
        await _agent(llm).invoke(user_input="你好")


@pytest.mark.asyncio
async def test_model_that_never_stops_calling_tools_is_bounded():
    """模型永遠不停手時，Gemini 呼叫次數有界，而不是跑到 10007 步。"""
    llm = _ScriptedLLM(tool_rounds=10_000)

    with pytest.raises(GraphRecursionError):
        await _agent(llm).invoke(user_input="你好")

    assert llm.invocations == MAX_TOOL_ROUNDS + 1
