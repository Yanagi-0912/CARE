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


def _location_state(allow_rag: bool = False, extra_messages=None):
    """重現使用者按下『分享位置資訊』後的那一輪。

    位置訊息由 LineLocationHandler 轉成文字後才進入 agent，
    且 Redis 不保存位置訊息與 ToolMessage，所以這一輪的 messages 是
    「前一輪的文字對話 + 本輪的位置文字」。
    """
    messages = [
        HumanMessage(content="我要醫院"),
        AIMessage(content="請點擊下方的『分享位置資訊』按鈕傳送您的位置，我馬上為您尋找附近的醫療院所！"),
        HumanMessage(content="這是我的目前位置：lat=24.7961, lng=120.9967"),
    ]
    if extra_messages:
        messages.extend(extra_messages)
    return {"messages": messages, "allow_rag": allow_rag}


@pytest.mark.asyncio
async def test_force_find_nearby_hospitals_when_user_shared_location(
    mock_llm_no_tool_calls, monkeypatch
):
    """
    Regression：使用者分享位置後模型若未主動呼叫工具，必須強制呼叫
    find_nearby_hospitals。否則 agent 回傳空內容，使用者只會看到
    「抱歉，我無法理解您的問題」。
    """
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(_location_state())

    response = res["messages"][0]
    assert response.tool_calls
    assert len(response.tool_calls) == 1
    tc = response.tool_calls[0]
    assert tc["name"] == "find_nearby_hospitals"
    assert tc["args"] == {"lat": 24.7961, "lng": 120.9967}
    assert tc["type"] == "tool_call"

    mock_log.assert_called_once()
    assert mock_log.call_args[1]["call"] == ["find_nearby_hospitals"]


@pytest.mark.asyncio
async def test_no_force_rag_for_shared_location_even_when_allow_rag(
    mock_llm_no_tool_calls, monkeypatch
):
    """位置文字不該被當成 RAG 查詢送出去。"""
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(_location_state(allow_rag=True))

    tc = res["messages"][0].tool_calls[0]
    assert tc["name"] == "find_nearby_hospitals"
    assert mock_log.call_args[1].get("force_rag") is None


@pytest.mark.asyncio
async def test_no_reforce_after_find_nearby_hospitals_ran(
    mock_llm_no_tool_calls, monkeypatch
):
    """工具跑完回到 agent 時不可再強制一次，否則會無限迴圈。"""
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = _location_state(
        allow_rag=True,
        extra_messages=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_nearby_hospitals",
                        "args": {"lat": 24.7961, "lng": 120.9967},
                        "id": "1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="附近院所清單",
                name="find_nearby_hospitals",
                tool_call_id="1",
            ),
        ],
    )

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.content == "腦補"
    assert not response.tool_calls


@pytest.mark.asyncio
async def test_llm_tool_choice_wins_over_forced_location_call(monkeypatch):
    """模型自己有呼叫工具時不介入。"""
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup_medical_facility",
                    "args": {"keyword": "台大醫院"},
                    "id": "9",
                    "type": "tool_call",
                }
            ],
        )
    )
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock())

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(_location_state())

    assert res["messages"][0].tool_calls[0]["name"] == "lookup_medical_facility"


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


# 模擬 LineMediaHandler 抽出的飲食指南 PDF 全文（含衛教用語「就醫」）。
_DIET_GUIDE_PDF_MEDIA_TEXT = (
    "以下為使用者傳送的file媒體內容：\n"
    "新版每日飲食指南\n"
    "均衡飲食有助維持健康。若有慢性病或特殊狀況，請諮詢醫師後再調整飲食。"
    "出現不適時再就醫評估。本指南不取代診所或醫院的個別營養建議。"
)


def test_diet_guide_pdf_text_is_not_nearby_facility_intent():
    """Regression：PDF 全文偶然出現『就醫／診所／醫院』≠ 使用者要找附近院所。"""
    assert _is_nearby_facility_intent(_DIET_GUIDE_PDF_MEDIA_TEXT) is False


@pytest.mark.asyncio
async def test_diet_guide_pdf_forces_rag_not_location(
    mock_llm_no_tool_calls, monkeypatch
):
    """Regression：上傳飲食指南 PDF 應走 RAG，不應強制分享位置找醫院。"""
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )

    nodes = AgentNodes(
        llm=mock_llm_no_tool_calls,
        guardrail_service=MagicMock(),
    )
    state = {
        "messages": [HumanMessage(content=_DIET_GUIDE_PDF_MEDIA_TEXT)],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage") as mock_log:
        res = await nodes.agent_node(state)

    response = res["messages"][0]
    assert response.tool_calls
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["name"] == "get_rag_answer"

    mock_log.assert_called_once()
    assert mock_log.call_args[1]["force_rag"] is True
    assert mock_log.call_args[1].get("force_location") is None
    assert mock_log.call_args[1]["call"] == ["get_rag_answer"]
