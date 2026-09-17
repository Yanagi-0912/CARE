"""本地「直接送 RAG」捷徑：有把握時不問 agent，其餘一律照舊。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.agent.utils.nodes import AgentNodes


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _mock_tools(include_rag_tool: bool = False):
    names = [
        "request_location_quick_reply",
        "find_nearby_hospitals",
        "suggest_department_for_symptom",
        "get_medication_status",
    ]
    if include_rag_tool:
        names += ["get_rag_answer", "answer_from_uploaded_document", "verify_claim"]
    return [_tool(name) for name in names]


class _Router:
    def __init__(self, probability: float, high: float = 0.8, recognized: bool = True):
        self.high = high
        self.low = 0.0
        self._probability = probability
        self._recognized = recognized
        self.calls: list[str] = []

    def probability(self, text: str) -> float:
        self.calls.append(text)
        return self._probability

    def recognizes(self, text: str) -> bool:
        return self._recognized


@pytest.fixture(autouse=True)
def _tools(monkeypatch):
    monkeypatch.setattr("app.services.agent.utils.nodes.get_all_tools", _mock_tools)


@pytest.fixture
def llm():
    model = MagicMock()
    model.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {"name": "suggest_department_for_symptom", "args": {}, "id": "m1", "type": "tool_call"}
            ],
        )
    )
    return model


def _state(text: str, *, allow_rag: bool = True, extra=None) -> dict:
    return {
        "messages": [*(extra or []), HumanMessage(content=text)],
        "allow_rag": allow_rag,
        "user_profile": None,
    }


def _only_call(result: dict) -> dict:
    (message,) = result["messages"]
    (call,) = message.tool_calls
    return call


@pytest.mark.asyncio
async def test_confident_router_sends_original_text_to_rag_without_asking_model(llm):
    router = _Router(probability=0.93)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)

    result = await nodes.agent_node(_state("血壓藥早上忘記吃，下午補吃可以嗎"))

    call = _only_call(result)
    assert call["name"] == "get_rag_answer"
    assert call["args"] == {"query": "血壓藥早上忘記吃，下午補吃可以嗎"}
    assert call["id"] == "shortcut_rag_1"
    llm.bind_tools.return_value.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_threshold_is_inclusive(llm):
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=_Router(0.8, high=0.8))

    result = await nodes.agent_node(_state("痛風可以吃什麼"))

    assert _only_call(result)["id"] == "shortcut_rag_1"


@pytest.mark.asyncio
async def test_below_high_still_asks_model(llm):
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=_Router(0.79, high=0.8))

    result = await nodes.agent_node(_state("胸口痛要看哪一科"))

    assert _only_call(result)["name"] == "suggest_department_for_symptom"
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrecognized_text_is_not_shortcut_even_with_high_probability(llm):
    # 語音辨識出不通順的句子，模型只認得一兩個片段，機率就是那幾個片段湊出來的。
    router = _Router(probability=0.99, recognized=False)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)

    await nodes.agent_node(_state("恥笑漸漸光，咱就大聲仔想著煞"))

    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_router_behaves_as_before(llm):
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock())

    await nodes.agent_node(_state("痛風可以吃什麼"))

    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_not_offered_when_guardrail_blocks_rag(llm):
    router = _Router(probability=0.99)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)

    await nodes.agent_node(_state("今天天氣如何", allow_rag=False))

    assert router.calls == []
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_not_used_after_tools_ran_this_turn(llm):
    # 工具回來之後那一步是在組回覆，不是在選工具。
    router = _Router(probability=0.99)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)
    llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="好的"))
    earlier = [
        HumanMessage(content="我阿嬤今天有吃藥嗎"),
        AIMessage(
            content="",
            tool_calls=[{"name": "get_medication_status", "args": {}, "id": "t1", "type": "tool_call"}],
        ),
        ToolMessage(content="已服用", name="get_medication_status", tool_call_id="t1"),
    ]
    state = {"messages": earlier, "allow_rag": True, "user_profile": None}

    await nodes.agent_node(state)

    assert router.calls == []
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.parametrize(
    "text",
    [
        "附近有沒有診所",  # 找附近院所
        "台大醫院電話多少",  # 指名院所
        "打開官網",  # 官網
        "我剛上傳的報告裡寫的數值是什麼意思",  # 上傳文件
        "這是我的目前位置：lat=25.03, lng=121.56",  # 分享位置
    ],
)
@pytest.mark.asyncio
async def test_deterministic_rules_win_before_router_is_consulted(llm, text):
    router = _Router(probability=0.99)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)

    await nodes.agent_node(_state(text))

    assert router.calls == []
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_failure_falls_back_to_model(llm):
    router = MagicMock()
    router.high = 0.5
    router.probability.side_effect = RuntimeError("boom")
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock(), rag_router=router)

    result = await nodes.agent_node(_state("痛風可以吃什麼"))

    assert _only_call(result)["name"] == "suggest_department_for_symptom"
