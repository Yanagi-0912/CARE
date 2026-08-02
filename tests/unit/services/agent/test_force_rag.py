import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from app.services.agent.utils.nodes import (
    AgentNodes,
    _is_named_facility_lookup,
    _is_nearby_facility_intent,
)


def _mock_tools(include_rag: bool = True):
    tools = []
    if include_rag:
        rag_tool = MagicMock()
        rag_tool.name = "get_rag_answer"
        tools.append(rag_tool)
    location_tool = MagicMock()
    location_tool.name = "request_location_quick_reply"
    tools.append(location_tool)
    hospital_tool = MagicMock()
    hospital_tool.name = "find_nearby_hospitals"
    tools.append(hospital_tool)
    return tools


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我要看醫院", True),
        ("附近有沒有診所", True),
        ("I need a hospital nearby", True),
        ("台大醫院在哪", False),
        ("台大醫院地址", False),
        ("我有六隻腳趾頭", False),
    ],
)
def test_is_nearby_facility_intent(text, expected):
    assert _is_nearby_facility_intent(text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("台大醫院在哪", True),
        ("診所電話多少", True),
        ("我要看醫院", False),
        ("我有六隻腳趾頭", False),
    ],
)
def test_is_named_facility_lookup(text, expected):
    assert _is_named_facility_lookup(text) is expected


@pytest.fixture
def mock_llm_no_tool_calls():
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="腦補")
    )
    return llm


@pytest.mark.asyncio
async def test_force_rag_when_allow_rag_and_no_tool_calls(mock_llm_no_tool_calls, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [HumanMessage(content="我有六隻腳趾頭")],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.tool_calls
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc["name"] == "get_rag_answer"
    assert tc["args"]["query"] == "我有六隻腳趾頭"
    assert tc["id"] == "forced_rag_1"
    assert tc["type"] == "tool_call"

    mock_log.assert_called_once()
    assert mock_log.call_args[0][1] == "agent_decide"
    assert mock_log.call_args[1]["force_rag"] is True
    assert mock_log.call_args[1]["call"] == ["get_rag_answer"]


@pytest.mark.asyncio
async def test_no_force_rag_when_already_ran_rag(mock_llm_no_tool_calls, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [
            HumanMessage(content="我有六隻腳趾頭"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_rag_answer",
                        "args": {"query": "我有六隻腳趾頭"},
                        "id": "1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="RAG 結果", name="get_rag_answer", tool_call_id="1"),
        ],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.content == "腦補"
    assert not response.tool_calls

    mock_log.assert_called_once()
    assert mock_log.call_args[1].get("force_rag") is None


@pytest.mark.asyncio
async def test_no_force_rag_after_request_location_quick_reply(
    mock_llm_no_tool_calls, monkeypatch
):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [
            HumanMessage(content="我要看醫院"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_location_quick_reply",
                        "args": {},
                        "id": "1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="已請求位置",
                name="request_location_quick_reply",
                tool_call_id="1",
            ),
        ],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.content == "腦補"
    assert not response.tool_calls

    mock_log.assert_called_once()
    assert mock_log.call_args[1].get("force_rag") is None


@pytest.mark.asyncio
async def test_force_location_when_hospital_seeking_intent(
    mock_llm_no_tool_calls, monkeypatch
):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [HumanMessage(content="我要看醫院")],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.tool_calls
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc["name"] == "request_location_quick_reply"
    assert tc["args"] == {}
    assert tc["id"] == "forced_location_1"
    assert tc["type"] == "tool_call"

    mock_log.assert_called_once()
    assert mock_log.call_args[1]["force_location"] is True
    assert mock_log.call_args[1].get("force_rag") is None
    assert mock_log.call_args[1]["call"] == ["request_location_quick_reply"]


@pytest.mark.asyncio
async def test_no_force_location_or_rag_for_named_facility_lookup(
    mock_llm_no_tool_calls, monkeypatch
):
    """Named lookup (在哪/地址) with facility terms: let LLM choose lookup tools."""
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [HumanMessage(content="台大醫院在哪")],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.content == "腦補"
    assert not response.tool_calls

    mock_log.assert_called_once()
    assert mock_log.call_args[1].get("force_rag") is None
    assert mock_log.call_args[1].get("force_location") is None


@pytest.mark.asyncio
async def test_no_force_rag_when_allow_rag_false(mock_llm_no_tool_calls, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [HumanMessage(content="我有六隻腳趾頭")],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.content == "腦補"
    assert not response.tool_calls

    mock_log.assert_called_once()
    assert mock_log.call_args[1].get("force_rag") is None
