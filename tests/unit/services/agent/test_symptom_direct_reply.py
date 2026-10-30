"""科別卡完成後即回覆；第二次模型呼叫逾時不應讓成品遺失。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.services.agent.agent import Agent, _route_after_tools
from app.tools.symptom_tools import SymptomPatientCase


TOOL_NAME = "suggest_department_for_symptom"
FLEX = (
    '{"type":"flex","altText":"建議的看診方向",'
    '"contents":{"type":"bubble","body":{"type":"box",'
    '"layout":"vertical","contents":[{"type":"text","text":"家醫科"}]}}}'
)


@pytest.mark.parametrize("allow_rag", [False, True])
@pytest.mark.parametrize(
    "answer",
    [FLEX, "建議先看家醫科。", "找到多位符合的家人，請先告訴我是誰。"],
    ids=["flex", "text", "clarification"],
)
@pytest.mark.asyncio
async def test_completed_symptom_reply_survives_a_failing_second_model_call(
    monkeypatch, allow_rag, answer
):
    @tool
    async def suggest_department_for_symptom(cases: list[SymptomPatientCase]) -> str:
        """回傳完整的科別建議或人物釐清問題。"""
        assert cases[0].symptom == "頭暈"
        return answer

    def offered(include_rag_tool=False):
        return [suggest_department_for_symptom]

    monkeypatch.setattr("app.services.agent.agent.get_all_tools", offered)
    monkeypatch.setattr("app.services.agent.utils.nodes.get_all_tools", offered)
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": TOOL_NAME,
                        "args": {"cases": [{"symptom": "頭暈"}]},
                        "id": "symptom-1",
                        "type": "tool_call",
                    }
                ],
            ),
            RuntimeError("504 DEADLINE_EXCEEDED"),
        ]
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=allow_rag)

    result = await Agent(llm, guardrail).invoke(user_input="頭暈要看哪一科？")

    assert result["response"] == answer
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


def test_symptom_tool_validation_error_still_returns_to_the_model():
    state = {
        "messages": [
            HumanMessage(content="頭暈要看哪一科？"),
            ToolMessage(
                content="Error invoking tool",
                name=TOOL_NAME,
                tool_call_id="symptom-1",
                status="error",
            ),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "agent"


def test_symptom_result_with_another_tool_still_returns_to_the_model():
    state = {
        "messages": [
            HumanMessage(content="頭暈要看哪一科？"),
            ToolMessage(content=FLEX, name=TOOL_NAME, tool_call_id="symptom-1"),
            ToolMessage(content="衛教資訊", name="get_rag_answer", tool_call_id="rag-1"),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "agent"
