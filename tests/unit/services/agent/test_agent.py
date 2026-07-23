import pytest
from unittest.mock import MagicMock, AsyncMock
from langchain_core.messages import HumanMessage, AIMessage

from app.services.agent.agent import Agent


@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def mock_guardrail_service():
    return MagicMock()


@pytest.mark.asyncio
async def test_agent_invoke_accepts_user_profile_kwarg(mock_llm, mock_guardrail_service):
    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={"messages": [AIMessage(content="ok")]}
    )

    response = await agent.invoke(
        user_input="Hello",
        messages=None,
        user_profile={"settings": {"voice_reply_enabled": True}},
    )

    assert response["response"] == "ok"


@pytest.mark.asyncio
async def test_agent_invoke_no_messages_provided(mock_llm, mock_guardrail_service):
    # Setup Agent with mock graph
    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    
    # Mock graph.ainvoke to return a mock structure containing final AI reply
    mock_result = {
        "messages": [
            HumanMessage(content="Hello"),
            AIMessage(content="AI reply")
        ]
    }
    agent._graph.ainvoke = AsyncMock(return_value=mock_result)

    response = await agent.invoke(user_input="Hello", messages=None)
    
    assert response["response"] == "AI reply"
    # Verify graph.ainvoke was called with messages list containing only the user input
    agent._graph.ainvoke.assert_called_once()
    called_state = agent._graph.ainvoke.call_args[0][0]
    assert len(called_state["messages"]) == 1
    assert called_state["messages"][0].content == "Hello"
    assert isinstance(called_state["messages"][0], HumanMessage)


@pytest.mark.asyncio
async def test_agent_invoke_with_history_missing_current_input(mock_llm, mock_guardrail_service):
    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    
    mock_result = {
        "messages": [
            HumanMessage(content="A"),
            AIMessage(content="B"),
            HumanMessage(content="C"),
            AIMessage(content="D")
        ]
    }
    agent._graph.ainvoke = AsyncMock(return_value=mock_result)

    # Mock history list (doesn't contain "C")
    history = [
        HumanMessage(content="A"),
        AIMessage(content="B")
    ]

    response = await agent.invoke(user_input="C", messages=history)
    
    assert response["response"] == "D"
    agent._graph.ainvoke.assert_called_once()
    called_state = agent._graph.ainvoke.call_args[0][0]
    
    # It should have appended "C" to the messages sent to the graph
    assert len(called_state["messages"]) == 3
    assert called_state["messages"][0].content == "A"
    assert called_state["messages"][1].content == "B"
    assert called_state["messages"][2].content == "C"
    assert isinstance(called_state["messages"][2], HumanMessage)


@pytest.mark.asyncio
async def test_agent_invoke_with_history_already_containing_current_input(mock_llm, mock_guardrail_service):
    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    
    mock_result = {
        "messages": [
            HumanMessage(content="A"),
            AIMessage(content="B"),
            HumanMessage(content="C"),
            AIMessage(content="D")
        ]
    }
    agent._graph.ainvoke = AsyncMock(return_value=mock_result)

    # Mock history list (already contains "C", e.g. location message or empty history list edge case)
    history = [
        HumanMessage(content="A"),
        AIMessage(content="B"),
        HumanMessage(content="C")
    ]

    response = await agent.invoke(user_input="C", messages=history)
    
    assert response["response"] == "D"
    agent._graph.ainvoke.assert_called_once()
    called_state = agent._graph.ainvoke.call_args[0][0]
    
    # It should NOT have duplicated "C"
    assert len(called_state["messages"]) == 3
    assert called_state["messages"][0].content == "A"
    assert called_state["messages"][1].content == "B"
    assert called_state["messages"][2].content == "C"


def test_format_user_profile_prompt_builds_expected_header():
    from app.services.agent.utils.nodes import format_user_profile_prompt

    assert format_user_profile_prompt(None) == ""
    assert format_user_profile_prompt({}) == ""

    profile = {
        "name": "王大明",
        "gender": "male",
        "age": 68,
        "height": 165.0,
        "weight": 62.0,
        "chronic_history": "高血壓",
        "major_illness_history": "無",
        "surgery_history": "無",
    }
    result = format_user_profile_prompt(profile)
    assert "王大明" in result
    assert "68 歲" in result
    assert "高血壓" in result
    assert "【對話使用者的個人健康與病史檔案】" in result


@pytest.mark.asyncio
async def test_agent_node_injects_user_profile_prompt():
    from app.services.agent.utils.nodes import AgentNodes

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="Hello 王大明")
    )
    mock_guardrail = MagicMock()

    nodes = AgentNodes(
        llm=mock_llm,
        guardrail_service=mock_guardrail,
        prompt_instruction="System Prompt Instruction",
    )

    state = {
        "messages": [HumanMessage(content="你好")],
        "allow_rag": False,
        "user_profile": {"name": "王大明", "age": 70, "chronic_history": "糖尿病"},
    }

    res = await nodes.agent_node(state)
    assert len(res["messages"]) == 1

    invoked_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_msg = invoked_messages[0]
    assert "System Prompt Instruction" in system_msg.content
    assert "【對話使用者的個人健康與病史檔案】" in system_msg.content
    assert "王大明" in system_msg.content
    assert "糖尿病" in system_msg.content

