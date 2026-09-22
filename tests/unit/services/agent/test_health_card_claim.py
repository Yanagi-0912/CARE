"""長輩傳謠言圖或衛教圖卡：圖上的主張先查核，查核資料庫沒收錄再送知識庫。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.services.agent.agent import Agent
from app.services.agent.utils.nodes import AgentNodes, _health_card_claim
from app.services.rag.claim_verification.service import VerificationResult
from app.tools import claim_tools
from app.tools.claim_tools import (
    CARD_CLAIM_NOT_FOUND,
    HEALTH_CARD_CLAIM_CALL_ID,
    verify_claim,
)

MEDIA_PREFIX = "以下為使用者傳送的image媒體內容："
CLAIM = "甜柿和螃蟹一起吃會中毒"
CARD = f"{MEDIA_PREFIX}\n【健康圖卡】\n圖卡主張：{CLAIM}\n\n甜柿&螃蟹一起吃會中毒？"
RAG_ANSWER = "柿子和螃蟹一起吃不會中毒 [1]。\n\n參考資料來源：\n[1] 食藥署"


def _tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _tools_factory(rag_names=("get_rag_answer", "verify_claim")):
    def _mock_tools(include_rag_tool: bool = False):
        names = ["request_location_quick_reply", "find_nearby_hospitals"]
        if include_rag_tool:
            names += list(rag_names)
        return [_tool(n) for n in names]

    return _mock_tools


@pytest.fixture
def llm():
    """模型沒呼叫工具：走到模型就代表沒有被決定性地送去查。"""
    model = MagicMock()
    model.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="圖上寫著⋯"))
    return model


def _state(messages, *, allow_rag: bool = True) -> dict:
    return {"messages": messages, "allow_rag": allow_rag, "user_profile": None}


async def _run(monkeypatch, llm, state, tools=None) -> dict:
    monkeypatch.setattr("app.services.agent.utils.nodes.get_all_tools", tools or _tools_factory())
    monkeypatch.setattr("app.services.agent.utils.nodes.log_stage", lambda *a, **k: None)
    return await AgentNodes(llm=llm, guardrail_service=MagicMock()).agent_node(state)


def test_只認有媒體前綴的圖卡主張():
    assert _health_card_claim(CARD) == CLAIM
    # 使用者自己打這幾個字不算圖卡
    assert _health_card_claim(f"【健康圖卡】\n圖卡主張：{CLAIM}") is None
    assert _health_card_claim(f"{MEDIA_PREFIX}\n【健康圖卡】\n") is None
    assert _health_card_claim(f"{MEDIA_PREFIX}\n血壓 120/80") is None


@pytest.mark.asyncio
async def test_圖卡主張直接送查核_不問模型(monkeypatch, llm):
    result = await _run(monkeypatch, llm, _state([HumanMessage(content=CARD)]))
    (call,) = result["messages"][0].tool_calls
    assert call["name"] == "verify_claim"
    assert call["args"] == {"query": CLAIM}
    assert call["id"] == HEALTH_CARD_CLAIM_CALL_ID
    llm.bind_tools.return_value.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_沒有查核工具時直接送知識庫(monkeypatch, llm):
    result = await _run(
        monkeypatch, llm, _state([HumanMessage(content=CARD)]),
        tools=_tools_factory(rag_names=("get_rag_answer",)),
    )
    (call,) = result["messages"][0].tool_calls
    assert call["name"] == "get_rag_answer"
    assert call["args"] == {"query": CLAIM}


@pytest.mark.asyncio
async def test_guardrail沒放行時照舊交給模型(monkeypatch, llm):
    result = await _run(monkeypatch, llm, _state([HumanMessage(content=CARD)], allow_rag=False))
    assert not getattr(result["messages"][0], "tool_calls", None)
    llm.bind_tools.return_value.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_查核資料庫沒收錄時改送知識庫(monkeypatch, llm):
    messages = [
        HumanMessage(content=CARD),
        AIMessage(content="", tool_calls=[{
            "name": "verify_claim", "args": {"query": CLAIM},
            "id": "health_card_claim_1", "type": "tool_call",
        }]),
        ToolMessage(content=CARD_CLAIM_NOT_FOUND, tool_call_id="health_card_claim_1", name="verify_claim"),
    ]
    result = await _run(monkeypatch, llm, _state(messages))
    (call,) = result["messages"][0].tool_calls
    assert call["name"] == "get_rag_answer"
    assert call["args"] == {"query": CLAIM}


@pytest.mark.asyncio
async def test_命中時不再送知識庫(monkeypatch, llm):
    messages = [
        HumanMessage(content=CARD),
        AIMessage(content="", tool_calls=[{
            "name": "verify_claim", "args": {"query": CLAIM},
            "id": "health_card_claim_1", "type": "tool_call",
        }]),
        ToolMessage(content='{"type": "flex"}', tool_call_id="health_card_claim_1", name="verify_claim"),
    ]
    result = await _run(monkeypatch, llm, _state(messages))
    assert not getattr(result["messages"][0], "tool_calls", None)


# ── 整條 graph：真的 ToolNode、真的 verify_claim，驗證圖卡模式認得出來 ──


def _result(matched: bool) -> VerificationResult:
    return VerificationResult(
        user_question=CLAIM,
        verdict="部分錯誤" if matched else "證據不足",
        verdict_slug="partially_false" if matched else "not_enough_evidence",
        reasoning="理由",
        source_title="食藥署闢謠" if matched else "",
        source_url="https://www.fda.gov.tw/x" if matched else "",
        source_published_at="",
        matched=matched,
        related_info="",
    )


def _graph_agent(monkeypatch, *, matched: bool):
    service = MagicMock()
    service.verify = AsyncMock(return_value=_result(matched))
    monkeypatch.setattr(claim_tools, "_claim_verification_service", service)

    rag_calls: list[str] = []

    @tool
    async def get_rag_answer(query: str) -> str:
        """假的知識庫。"""
        rag_calls.append(query)
        return RAG_ANSWER

    def _tools(include_rag_tool: bool = False):
        return [verify_claim, get_rag_answer] if include_rag_tool else []

    monkeypatch.setattr("app.services.agent.agent.get_all_tools", _tools)
    monkeypatch.setattr("app.services.agent.utils.nodes.get_all_tools", _tools)

    model = MagicMock()
    # 命中後 graph 會回模型組一次回覆（最後仍以判定卡為準）
    model.bind_tools.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="模型的話"))
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=True)
    return Agent(llm=model, guardrail_service=guardrail), service, rag_calls


@pytest.mark.asyncio
async def test_整條流程_沒收錄時回知識庫答案而不是證據不足卡(monkeypatch):
    agent, service, rag_calls = _graph_agent(monkeypatch, matched=False)

    response = await agent.invoke(user_input=CARD, messages=None)

    service.verify.assert_awaited_once_with(CLAIM, related_on_miss=False)
    assert rag_calls == [CLAIM]
    assert RAG_ANSWER.split("\n")[0] in response["response"]
    assert "證據不足" not in response["response"]
    assert CARD_CLAIM_NOT_FOUND not in response["response"]


@pytest.mark.asyncio
async def test_整條流程_收錄過時回判定卡(monkeypatch):
    agent, service, rag_calls = _graph_agent(monkeypatch, matched=True)

    response = await agent.invoke(user_input=CARD, messages=None)

    assert rag_calls == []
    payload = json.loads(response["response"])
    assert payload["type"] == "flex"
    assert "部分錯誤" in payload["altText"]


@pytest.mark.asyncio
async def test_模型自己呼叫的查核照舊回證據不足卡(monkeypatch):
    """圖卡模式只認捷徑的 id；打字問的查核沒收錄時仍是判定卡。"""
    service = MagicMock()
    service.verify = AsyncMock(return_value=_result(False))
    monkeypatch.setattr(claim_tools, "_claim_verification_service", service)

    out = await verify_claim.ainvoke(
        {"name": "verify_claim", "args": {"query": CLAIM}, "id": "call_abc", "type": "tool_call"}
    )

    service.verify.assert_awaited_once_with(CLAIM)
    assert out.content != CARD_CLAIM_NOT_FOUND
    assert "證據不足" in out.content
