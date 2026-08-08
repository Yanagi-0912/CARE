"""「現在有開的」意圖偵測與跨輪保留。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.agent.utils.nodes import (
    AgentNodes,
    _is_open_now_intent,
    _wants_open_now_from_history,
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
        ("附近現在有開的診所嗎", True),
        ("哪裡還在看診", True),
        ("現在營業中的醫院", True),
        ("附近有正在營業的藥局嗎", True),
        ("any clinic open now", True),
        # 沒有明說就不能推論 —— 午休時段只有 11.5% 營業，預設篩選會刪掉太多
        ("附近有診所嗎", False),
        ("附近有腸胃科嗎", False),
        ("這家診所的營業時間", False),
        ("", False),
    ],
)
def test_is_open_now_intent(text, expected):
    assert _is_open_now_intent(text) is expected


def test_wants_open_now_looks_past_location_message():
    messages = [
        HumanMessage(content="附近現在有開的診所嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _wants_open_now_from_history(messages) is True


def test_wants_open_now_false_without_explicit_request():
    messages = [
        HumanMessage(content="附近有診所嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _wants_open_now_from_history(messages) is False


def test_wants_open_now_ignores_stale_request():
    messages = [
        HumanMessage(content="附近現在有開的診所嗎"),
        HumanMessage(content="謝謝"),
        HumanMessage(content="今天天氣如何"),
        HumanMessage(content="血壓多少算高"),
        HumanMessage(content="幫我看一下"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _wants_open_now_from_history(messages) is False


@pytest.mark.asyncio
async def test_shared_location_carries_open_now(
    mock_llm_no_tool_calls, patched_tools
):
    """
    刻意用裸詞「醫院」而非「診所」：本測試只聚焦 open_now 是否單獨成立，
    「診所」是 Task 4 新增的明確類型語彙，會多帶出 facility_type，
    與此處想驗證的行為無關（兩者並存的情境見 test_facility_type_intent.py）。
    """
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近現在有開的醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56, "open_now": True}


@pytest.mark.asyncio
async def test_open_now_and_department_combine(
    mock_llm_no_tool_calls, patched_tools
):
    """兩個限定各自獨立，可同時成立。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近現在有開的腸胃科嗎"),
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
        "open_now": True,
    }


@pytest.mark.asyncio
async def test_no_open_now_arg_when_not_requested(
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
    assert "open_now" not in call["args"]
