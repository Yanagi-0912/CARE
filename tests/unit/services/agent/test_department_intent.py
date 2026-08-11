"""
科別搜尋在 agent 決策層的行為。

重點在跨輪：使用者提到科別的那一輪還沒有座標，等座標進來時最新的一則
HumanMessage 已經是系統轉出的「這是我的目前位置：lat=…」，科別落在更早的訊息裡。
若只看最後一則就會退化成搜尋所有科別，回傳一堆牙科、婦產科。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.agent.utils.nodes import (
    AgentNodes,
    _extract_department_from_history,
    _is_department_visit_intent,
    _is_nearby_department_intent,
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("附近有腸胃科嗎", True),
        ("最近的耳鼻喉科", True),
        ("哪裡有中醫", True),
        ("附近推薦的牙科", True),
        # 純症狀敘述不該跳出要位置的按鈕，使用者多半是想問衛教
        ("我牙齒痛", False),
        ("我腸胃不舒服", False),
        # 缺鄰近詞
        ("腸胃科是看什麼的", False),
        # 查特定院所走 lookup_medical_facility
        ("附近的台大醫院在哪", False),
        ("", False),
    ],
)
def test_is_nearby_department_intent(text, expected):
    assert _is_nearby_department_intent(text) is expected


def test_department_intent_also_counts_as_nearby_facility_intent():
    """「附近有腸胃科嗎」不含醫院／診所字眼，仍須觸發要位置的流程。"""
    assert _is_nearby_facility_intent("附近有腸胃科嗎") is True
    assert _is_nearby_facility_intent("附近有醫院嗎") is True


def test_extract_department_from_history_looks_past_location_message():
    messages = [
        HumanMessage(content="附近有腸胃科嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_department_from_history(messages) == "腸胃科"


def test_extract_department_from_history_returns_none_without_department():
    messages = [
        HumanMessage(content="附近有醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_department_from_history(messages) is None


def test_extract_department_from_history_ignores_stale_mentions():
    """太久以前提過的科別不該套用到這次搜尋。"""
    messages = [
        HumanMessage(content="附近有腸胃科嗎"),
        HumanMessage(content="謝謝"),
        HumanMessage(content="今天天氣如何"),
        HumanMessage(content="血壓多少算高"),
        HumanMessage(content="幫我量一下"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_department_from_history(messages) is None


@pytest.mark.asyncio
async def test_shared_location_carries_department_from_previous_turn(
    mock_llm_no_tool_calls, patched_tools
):
    """這是這個功能最容易壞的地方：科別必須跨輪帶到座標那一輪。"""
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

    tool_calls = res["messages"][0].tool_calls
    assert len(tool_calls) == 1
    call = tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "腸胃科",
    }


@pytest.mark.asyncio
async def test_shared_location_without_department_uses_general_search(
    mock_llm_no_tool_calls, patched_tools
):
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


@pytest.mark.asyncio
async def test_department_question_without_location_requests_location(
    mock_llm_no_tool_calls, patched_tools
):
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [HumanMessage(content="附近有腸胃科嗎")],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "request_location_quick_reply"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 就診意圖不需要鄰近詞：說「我要看大腸科」就是要看這一科
        ("我要看大腸科", True),
        ("我要看腸胃科", True),
        ("幫我掛腸胃科", True),
        ("我要掛號皮膚科", True),
        ("我想預約眼科門診", True),
        ("我要看小兒科的醫生", True),
        ("我想看牙醫", True),
        # 別名表未收錄也要成立，否則查表落空就掉回 RAG
        ("我要看腹腔鏡科", True),
        # 衛教問句：句中有「看」但不是要就診
        ("腸胃科是看什麼的", False),
        ("大腸科在看什麼", False),
        ("腸胃科要空腹嗎", False),
        # 轉述就醫經過後追問衛教，動詞雖緊接科別，但科別後面還有問題
        ("上次看腸胃科醫師說要多喝水這樣對嗎", False),
        # 純症狀敘述
        ("我牙齒痛", False),
        ("我腸胃不舒服", False),
        # 字面上是「X科」但不是科別
        ("我要看理科", False),
        ("我要看科技新知", False),
        ("", False),
    ],
)
def test_is_department_visit_intent(text, expected):
    assert _is_department_visit_intent(text) is expected


def test_department_visit_intent_also_counts_as_nearby_facility_intent():
    """就診意圖必須走要位置的流程，不能落入強制 RAG。"""
    assert _is_nearby_facility_intent("我要看大腸科") is True
    assert _is_nearby_facility_intent("幫我掛腸胃科") is True


@pytest.mark.asyncio
async def test_visit_intent_requests_location_instead_of_rag(
    mock_llm_no_tool_calls, patched_tools
):
    """
    線上實際案例：使用者打「我要看大腸科」，allow_rag 為真時整句被強制送進
    get_rag_answer，回來的是一段「大腸直腸科醫師在看什麼」的衛教說明。
    """
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [HumanMessage(content="我要看大腸科")],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "request_location_quick_reply"


@pytest.mark.asyncio
async def test_visit_intent_department_carries_to_location_turn(
    mock_llm_no_tool_calls, patched_tools
):
    """座標進來時要沿用上一輪說的科別，否則會退化成搜尋所有科別。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="我要看大腸科"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": True,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "大腸科",
    }
