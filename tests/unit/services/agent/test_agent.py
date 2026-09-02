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


@pytest.mark.asyncio
async def test_agent_uses_verify_claim_flex_json_verbatim_as_final_response(
    mock_llm, mock_guardrail_service
):
    """verify_claim 回傳的是要直接送去 LINE 的 Flex JSON（task 7）。工具執行後
    agent 節點一定會再呼叫一次 LLM 產生「最終回覆」；若放任那一輪的自由文字
    當成回覆，使用者看到的會是模型描述 JSON 的大白話，不是真正的判定卡。

    比照 open_official_site 的做法：medical_tool_names 這個集合會在
    Agent.invoke 收尾時，把符合名單的 ToolMessage 內容原封不動當成最終回覆，
    無視同一輪 LLM 自己說了什麼。這裡鎖住 verify_claim 也在這個名單裡。
    """
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    flex_json = '{"type": "flex", "altText": "查核判定：錯誤", "contents": {}}'
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="網傳吃鳳梨心可以溶解血栓，是真的嗎？"),
                ToolMessage(
                    content=flex_json,
                    tool_call_id="1",
                    name="verify_claim",
                ),
                AIMessage(content="這則傳言查核後被認定為錯誤消息。"),
            ]
        }
    )

    response = await agent.invoke(
        user_input="網傳吃鳳梨心可以溶解血栓，是真的嗎？", messages=None
    )

    assert response["response"] == flex_json


@pytest.mark.asyncio
async def test_agent_keeps_flex_json_intact_when_rag_tool_also_ran(
    mock_llm, mock_guardrail_service
):
    """模型同一輪既呼叫 verify_claim 又呼叫 get_rag_answer 時，來源後補不得動到
    Flex JSON。

    這是線上實際發生過的回歸：medical_tool_names 先把 response 覆寫成判定卡的
    Flex JSON，接著「後補參考資料來源」那段只檢查有沒有跑過 get_rag_answer，
    就把來源接在 JSON 尾巴後面。reply.py 的 `_try_parse_flex_message` 要求整串
    以 "{" 開頭、"}" 結尾才解析，結尾變成 URL 後解析失敗，使用者收到的是一整段
    裸 JSON 而不是卡片。斷言 response 逐字等於 flex_json，同時鎖住「沒有被接上
    任何東西」與「結尾仍是 }」這兩件事。
    """
    from app.i18n.messages import t
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    flex_json = '{"type": "flex", "altText": "查核判定：證據不足", "contents": {}}'
    heading = t("agent.sources_heading", "zh-TW")
    rag_tool_output = f"衛教內容。\n\n{heading}\n[1] 食藥署：https://www.fda.gov.tw/x"
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="網傳蜂蜜可以抗癌，是真的嗎？"),
                ToolMessage(
                    content=rag_tool_output,
                    tool_call_id="1",
                    name="get_rag_answer",
                ),
                ToolMessage(
                    content=flex_json,
                    tool_call_id="2",
                    name="verify_claim",
                ),
                AIMessage(content="這則說法查核中心尚未查證。"),
            ]
        }
    )

    response = await agent.invoke(
        user_input="網傳蜂蜜可以抗癌，是真的嗎？", messages=None
    )

    assert response["response"] == flex_json
    assert heading not in response["response"]


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
        "chronic_diseases": ["hypertension", "diabetes"],
        "chronic_custom": ["痛風"],
        "major_illness_history": "",
        "surgery_history": "",
    }
    result = format_user_profile_prompt(profile)
    assert "王大明" in result
    assert "68 歲" in result
    assert "性別：男" in result
    # 固定選項還原成中文，使用者自行輸入的接在後面且原文照用
    assert "慢性病史：高血壓、糖尿病、痛風" in result
    assert "【對話使用者的個人健康與病史檔案】" in result


def test_format_user_profile_prompt_never_claims_none_when_data_exists():
    """回歸測試：有病史時，prompt 絕不可以說「無」。

    「慢性病史：無」是一句合法通順的臨床斷言，LLM 無從分辨它是「確認沒有」
    還是「資料讀不到」。欄位改名或改型別時，這裡是唯一會攔下靜默失效的地方。
    """
    from app.services.agent.utils.nodes import format_user_profile_prompt

    only_fixed = format_user_profile_prompt({"name": "A", "chronic_diseases": ["hypertension"]})
    assert "慢性病史：高血壓" in only_fixed
    assert "慢性病史：無" not in only_fixed

    only_custom = format_user_profile_prompt({"name": "A", "chronic_custom": ["腦溢血"]})
    assert "慢性病史：腦溢血" in only_custom
    assert "慢性病史：無" not in only_custom

    # 認不得的 code 原樣輸出，寧可讓 prompt 出現代碼也不能少掉一項病史
    unknown = format_user_profile_prompt({"name": "A", "chronic_diseases": ["gout_v2"]})
    assert "慢性病史：gout_v2" in unknown

    # 兩者皆空才說「無」
    empty = format_user_profile_prompt(
        {"name": "A", "chronic_diseases": [], "chronic_custom": []}
    )
    assert "慢性病史：無" in empty


@pytest.mark.asyncio
async def test_agent_node_uses_profile_language_for_system_prompt():
    from app.i18n.messages import t
    from app.services.agent.utils.nodes import AgentNodes

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="Hello")
    )
    mock_guardrail = MagicMock()

    nodes = AgentNodes(llm=mock_llm, guardrail_service=mock_guardrail)

    state = {
        "messages": [HumanMessage(content="hi")],
        "allow_rag": False,
        "user_profile": {"settings": {"language": "en"}},
    }

    await nodes.agent_node(state)

    invoked_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_msg = invoked_messages[0]
    assert "English" in system_msg.content
    assert t("agent.rag_prefix", "en") in system_msg.content
    assert "必須只使用繁體中文" not in system_msg.content


@pytest.mark.asyncio
async def test_agent_node_defaults_to_zh_tw_when_profile_has_no_language():
    from app.services.agent.utils.nodes import AgentNodes

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="你好")
    )
    mock_guardrail = MagicMock()

    nodes = AgentNodes(llm=mock_llm, guardrail_service=mock_guardrail)

    state = {
        "messages": [HumanMessage(content="你好")],
        "allow_rag": False,
        "user_profile": {"name": "王大明"},
    }

    await nodes.agent_node(state)

    invoked_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_msg = invoked_messages[0]
    assert "繁體中文" in system_msg.content


@pytest.mark.asyncio
async def test_agent_strips_fabricated_sources_when_tool_has_none(
    mock_llm, mock_guardrail_service
):
    from app.i18n.messages import t
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    zh_heading = t("agent.sources_heading", "zh-TW")
    rag_tool_output = "以下參考網路公開資料\n\nRAG 正文，無來源標題。"
    fabricated_response = (
        f"以下為 RAG 回應：\n\n根據資料，建議就醫。\n\n{zh_heading}\n"
        "[1] 假來源: https://fake.example/a\n"
        "[2] 假來源: https://fake.example/b"
    )
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="症狀問題"),
                ToolMessage(
                    content=rag_tool_output,
                    tool_call_id="1",
                    name="get_rag_answer",
                ),
                AIMessage(content=fabricated_response),
            ]
        }
    )

    response = await agent.invoke(user_input="症狀問題", messages=None)

    assert zh_heading not in response["response"]
    assert "fake.example" not in response["response"]
    assert "根據資料，建議就醫。" in response["response"]


@pytest.mark.asyncio
async def test_agent_appends_localized_sources_heading_when_missing(mock_llm, mock_guardrail_service):
    from app.core.user_language import reset_request_language, set_request_language
    from app.i18n.messages import t
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    en_heading = t("agent.sources_heading", "en")
    rag_tool_output = (
        f"RAG body.\n\n{en_heading}\n[1] CDC: https://www.cdc.gov.tw/x"
    )
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="question"),
                ToolMessage(
                    content=rag_tool_output,
                    tool_call_id="1",
                    name="get_rag_answer",
                ),
                AIMessage(content="Short answer without sources."),
            ]
        }
    )

    token = set_request_language("en")
    try:
        response = await agent.invoke(user_input="question", messages=None)
    finally:
        reset_request_language(token)

    assert en_heading in response["response"]
    assert "[1] CDC: https://www.cdc.gov.tw/x" in response["response"]


@pytest.mark.asyncio
async def test_agent_node_injects_user_profile_prompt():
    from app.services.agent.utils.nodes import AgentNodes

    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="Hello 王大明")
    )
    mock_guardrail = MagicMock()

    nodes = AgentNodes(llm=mock_llm, guardrail_service=mock_guardrail)

    state = {
        "messages": [HumanMessage(content="你好")],
        "allow_rag": False,
        "user_profile": {
            "name": "王大明",
            "age": 70,
            "chronic_diseases": ["diabetes"],
        },
    }

    res = await nodes.agent_node(state)
    assert len(res["messages"]) == 1

    invoked_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_msg = invoked_messages[0]
    assert "繁體中文" in system_msg.content
    assert "【對話使用者的個人健康與病史檔案】" in system_msg.content
    assert "王大明" in system_msg.content
    assert "糖尿病" in system_msg.content

