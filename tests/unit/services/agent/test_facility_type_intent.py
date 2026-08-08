"""
院所類型（醫院／診所／藥局）在 agent 決策層的意圖偵測與跨輪保留。

重點：不能直接用 extract_facility_type_intent() 的結果 —— 它對裸詞「醫院」
會命中（「附近有醫院嗎」→ requested="醫院"），但「醫院」在口語中常泛指
「醫療院所」，若照單全收會把類型過濾套到單純想看病的使用者身上，
將 18,935 家診所全數排除。必須只在「大醫院」「住院」「診所」「藥局」等
明確語彙出現時才觸發，且與科別意圖一樣要能跨輪保留到座標那一則訊息。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.agent.utils.nodes import (
    AgentNodes,
    _extract_facility_type_from_history,
    _facility_type_intent,
    _is_nearby_facility_intent,
)

LOCATION_TEXT = "這是我的目前位置：lat=25.033, lng=121.56"


def _mock_tools(include_rag: bool = True):
    tools = []
    if include_rag:
        for name in ("get_rag_answer", "answer_from_uploaded_document"):
            tool = MagicMock()
            tool.name = name
            tools.append(tool)
    for name in (
        "request_location_quick_reply",
        "find_nearby_hospitals",
        "find_nearby_facilities_by_department",
        "open_official_site",
    ):
        tool = MagicMock()
        tool.name = name
        tools.append(tool)
    return tools


@pytest.fixture
def mock_llm_no_tool_calls():
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="腦補")
    )
    return llm


@pytest.fixture
def patched_tools(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )


# ---------------------------------------------------------------------------
# _facility_type_intent：嚴格閘門本身
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 明確語彙 → 觸發，且回傳使用者原始說法
        ("附近有大醫院嗎", "大醫院"),
        ("我要找大型醫院", "大型醫院"),
        ("附近有可以住院的地方嗎", "住院"),
        ("附近有診所嗎", "診所"),
        ("找一間小診所", "小診所"),
        ("附近有藥局嗎", "藥局"),
        ("哪裡有藥房", "藥房"),
        ("附近的藥店", "藥店"),
        # 裸詞「醫院」→ 泛稱，不觸發
        ("附近有醫院嗎", None),
        ("我要去醫院", None),
        ("醫院在哪", None),
        # 門診：判斷為與裸詞「醫院」同樣曖昧（醫院也有門診部），不觸發
        ("我要看門診", None),
        ("附近有門診嗎", None),
        # 牙醫／中醫屬於科別維度，本來就解析不出類型
        ("附近有牙醫嗎", None),
        ("哪裡有中醫", None),
        ("", None),
    ],
)
def test_facility_type_intent_gate(text, expected):
    assert _facility_type_intent(text) == expected


def test_nearby_medium_hospital_still_triggers_location_request():
    """4.3：「附近有大醫院嗎」應可觸發要位置的流程（醫院已在 _FACILITY_SEARCH_RE 內）。"""
    assert _is_nearby_facility_intent("附近有大醫院嗎") is True


# ---------------------------------------------------------------------------
# _extract_facility_type_from_history：跨輪保留
# ---------------------------------------------------------------------------


def test_extract_facility_type_from_history_looks_past_location_message():
    messages = [
        HumanMessage(content="附近有大醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) == "大醫院"


def test_extract_facility_type_from_history_returns_none_for_bare_hospital():
    messages = [
        HumanMessage(content="附近有醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) is None


def test_extract_facility_type_from_history_ignores_stale_mentions():
    """太久以前提過的類型需求不該套用到這次搜尋。"""
    messages = [
        HumanMessage(content="附近有大醫院嗎"),
        HumanMessage(content="謝謝"),
        HumanMessage(content="今天天氣如何"),
        HumanMessage(content="血壓多少算高"),
        HumanMessage(content="幫我量一下"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) is None


# ---------------------------------------------------------------------------
# agent_node：4.2 四種組合的注入行為
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_location_with_only_department(
    mock_llm_no_tool_calls, patched_tools
):
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有腸胃科嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "腸胃科",
    }


@pytest.mark.asyncio
async def test_shared_location_with_only_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "facility_type": "大醫院",
    }


@pytest.mark.asyncio
async def test_shared_location_with_department_and_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    """兩個維度各自獨立，可同時成立（例如「大醫院的腸胃科」）。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院的腸胃科嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "腸胃科",
        "facility_type": "大醫院",
    }


@pytest.mark.asyncio
async def test_shared_location_without_department_or_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    """向後相容：都沒有需求時，args 不得出現 facility_type 鍵。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56}
    assert "facility_type" not in call["args"]


@pytest.mark.asyncio
async def test_facility_type_can_combine_with_open_now(
    mock_llm_no_tool_calls, patched_tools
):
    """open_now 的既有行為不得改變，且應能與類型並存。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近現在有開的大醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "facility_type": "大醫院",
        "open_now": True,
    }


@pytest.mark.asyncio
async def test_stale_facility_type_not_carried_over(
    mock_llm_no_tool_calls, patched_tools
):
    """需求過時（超過回溯上限）不沿用。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院嗎"),
            HumanMessage(content="謝謝"),
            HumanMessage(content="今天天氣如何"),
            HumanMessage(content="血壓多少算高"),
            HumanMessage(content="幫我量一下"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56}
    assert "facility_type" not in call["args"]
