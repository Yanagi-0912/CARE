"""長輩拍電視新聞畫面：標題直接送查核，不照畫面複述。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.agent.utils.nodes import AgentNodes, _tv_news_claim

MEDIA_PREFIX = "以下為使用者傳送的image媒體內容："
TV_NEWS = (
    f"{MEDIA_PREFIX}\n"
    "【電視新聞畫面】\n"
    "電視台：TVBS\n"
    "新聞標題：維他命添色素‧影響智力"
)


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _tools_factory(
    *, rag_names=("get_rag_answer", "answer_from_uploaded_document", "verify_claim", "verify_tv_news")
):
    def _mock_tools(include_rag_tool: bool = False):
        names = ["request_location_quick_reply", "find_nearby_hospitals", "get_medication_status"]
        if include_rag_tool:
            names += list(rag_names)
        return [_tool(name) for name in names]

    return _mock_tools


@pytest.fixture
def llm():
    """模型沒呼叫工具：走到模型就代表沒有被決定性地送去查核。"""
    model = MagicMock()
    model.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="這是一則新聞"))
    return model


def _state(text: str, *, allow_rag: bool = True, extra=None) -> dict:
    return {
        "messages": [*(extra or []), HumanMessage(content=text)],
        "allow_rag": allow_rag,
        "user_profile": None,
    }


async def _run(monkeypatch, llm, state, tools=None) -> dict:
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools", tools or _tools_factory()
    )
    monkeypatch.setattr("app.services.agent.utils.nodes.log_stage", lambda *a, **k: None)
    nodes = AgentNodes(llm=llm, guardrail_service=MagicMock())
    return await nodes.agent_node(state)


def test_只抽主標題_字幕與台別不當成待查主張():
    assert _tv_news_claim(TV_NEWS) == "維他命添色素‧影響智力"
    assert _tv_news_claim(f"{MEDIA_PREFIX}\n【電視新聞畫面】\n電視台：TVBS\n畫面上沒有看到這則新聞的標題") is None
    # 沒有媒體前綴＝使用者自己打的字，照一般訊息處理
    assert _tv_news_claim("【電視新聞畫面】\n新聞標題：維他命添色素‧影響智力") is None
    assert _tv_news_claim(f"{MEDIA_PREFIX}\n血壓 120/80") is None


@pytest.mark.asyncio
async def test_電視新聞標題直接送查核並帶上台別(monkeypatch, llm):
    result = await _run(monkeypatch, llm, _state(TV_NEWS))

    (message,) = result["messages"]
    (call,) = message.tool_calls
    # verify_tv_news 的判定卡會多一顆「看新聞原文」，所以優先於 verify_claim。
    assert call["name"] == "verify_tv_news"
    # 送的是標題本身，不是整段畫面描述——描述會把台別與註記一起當成查核主張。
    assert call["args"] == {"headline": "維他命添色素‧影響智力", "channel": "TVBS"}
    llm.bind_tools.return_value.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_沒有電視新聞工具時退回一般查核(monkeypatch, llm):
    result = await _run(
        monkeypatch, llm, _state(TV_NEWS), tools=_tools_factory(rag_names=("get_rag_answer", "verify_claim"))
    )
    (call,) = result["messages"][0].tool_calls
    assert call["name"] == "verify_claim"
    assert call["args"] == {"query": "維他命添色素‧影響智力"}


@pytest.mark.asyncio
async def test_認不出台別時台別留空(monkeypatch, llm):
    """n8n 認不出台別時整行不會出現；留空讓工具只做查核、不找新聞。"""
    text = f"{MEDIA_PREFIX}\n【電視新聞畫面】\n新聞標題：維他命添色素‧影響智力"
    result = await _run(monkeypatch, llm, _state(text))
    (call,) = result["messages"][0].tool_calls
    assert call["args"] == {"headline": "維他命添色素‧影響智力", "channel": ""}


@pytest.mark.asyncio
async def test_標題不完整的註記不會被當成標題的一部分(monkeypatch, llm):
    text = TV_NEWS + "\n（標題可能不完整或有字看不清楚）"
    result = await _run(monkeypatch, llm, _state(text))
    (call,) = result["messages"][0].tool_calls
    assert call["args"]["headline"] == "維他命添色素‧影響智力"


@pytest.mark.asyncio
async def test_沒有查核工具時改送知識庫(monkeypatch, llm):
    result = await _run(
        monkeypatch, llm, _state(TV_NEWS), tools=_tools_factory(rag_names=("get_rag_answer",))
    )
    (call,) = result["messages"][0].tool_calls
    assert call["name"] == "get_rag_answer"
    assert call["args"] == {"query": "維他命添色素‧影響智力"}


@pytest.mark.asyncio
async def test_guardrail沒放行時維持照畫面描述(monkeypatch, llm):
    """颱風、選舉這類新聞拿不到知識庫工具，行為與導入前相同。"""
    result = await _run(monkeypatch, llm, _state(TV_NEWS, allow_rag=False))
    assert not getattr(result["messages"][0], "tool_calls", None)
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_查核跑完不再送第二次(monkeypatch, llm):
    """工具回來後那一步是在組回覆，再送一次就是無窮迴圈。"""
    extra = [
        AIMessage(content="", tool_calls=[{"name": "verify_claim", "args": {"query": "x"}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="{}", name="verify_claim", tool_call_id="1"),
    ]
    state = {
        "messages": [HumanMessage(content=TV_NEWS), *extra],
        "allow_rag": True,
        "user_profile": None,
    }
    result = await _run(monkeypatch, llm, state)
    assert not getattr(result["messages"][0], "tool_calls", None)


@pytest.mark.asyncio
async def test_一般圖片不受影響(monkeypatch, llm):
    """藥袋、表格、文件照舊由模型直接依內容回答（prompt 規則 (e)）。"""
    result = await _run(monkeypatch, llm, _state(f"{MEDIA_PREFIX}\n| 項目 | 值 |\n| --- | --- |"))
    assert not getattr(result["messages"][0], "tool_calls", None)
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()
