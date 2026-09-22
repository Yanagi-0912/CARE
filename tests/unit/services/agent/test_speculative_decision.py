"""
guardrail 節點期間先問模型選工具（nodes._start_speculative_decision）。

線上 guardrail 節點 p50 2.6 秒、選工具 p50 2.7 秒，以前串著跑。這裡驗的是：
模型呼叫在 guardrail 回來之前就起跑；放行時直接沿用；沒放行但沒選到 RAG 也
沿用；沒放行又選了 RAG 就重問；緊急時丟掉；本地捷徑判得出來就不問模型。
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.services.agent.agent import Agent
from app.services.medical.symptom_classification.urgency import (
    NOT_URGENT,
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)


def _call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"{name}_1", "type": "tool_call"}],
    )


class _LLM:
    """依序回劇本；記錄每次呼叫的起點與被綁的工具名。"""

    def __init__(self, *script):
        self.script = list(script)
        self.started: list[float] = []
        self.bound: list[list[str]] = []
        self.delay = 0.0

    def bind_tools(self, tools):
        self.bound.append([t.name for t in tools])
        return self

    async def ainvoke(self, _messages):
        self.started.append(asyncio.get_running_loop().time())
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script:
            return self.script.pop(0)
        return AIMessage(content="最終回覆")


class _Guardrail:
    def __init__(self, allow_rag=True, delay=0.0):
        self.allow_rag = allow_rag
        self.delay = delay
        self.finished_at: float | None = None

    async def allow_rag_tool(self, _text):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.finished_at = asyncio.get_running_loop().time()
        return self.allow_rag


class _Urgency:
    def __init__(self, verdict=NOT_URGENT, delay=0.0):
        self.verdict = verdict
        self.delay = delay

    async def classify(self, _text, *, language="繁體中文"):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.verdict


def _agent(llm, *, guardrail, urgency=None, rag_router=None):
    return Agent(
        llm=llm,
        guardrail_service=guardrail,
        urgency_classifier=urgency or _Urgency(),
        rag_router=rag_router,
    )


@pytest.mark.asyncio
async def test_model_is_asked_before_guardrail_finishes():
    guardrail = _Guardrail(allow_rag=True, delay=0.05)
    # 選家人名冊：工具回來後直通，不再問第二次，呼叫次數才好數。
    llm = _LLM(_call("get_family_directory"))

    result = await _agent(llm, guardrail=guardrail).invoke(user_input="我家人有誰")

    assert result["response"]
    assert len(llm.started) == 1
    assert llm.started[0] < guardrail.finished_at, "選工具沒有等 guardrail"
    assert "get_rag_answer" in llm.bound[0], "投機時假設 RAG 會放行、掛全套工具"


@pytest.mark.asyncio
async def test_denied_rag_but_other_tool_chosen_is_reused():
    """沒放行 RAG、模型選的是別的工具：那個決定在縮小的工具集裡一樣合法，不重問。"""
    llm = _LLM(_call("get_family_directory"))

    await _agent(llm, guardrail=_Guardrail(allow_rag=False)).invoke(user_input="我家人有誰")

    # 只有投機那一次；家人名冊直通，不再問模型。
    assert len(llm.started) == 1
    assert "get_rag_answer" in llm.bound[0]


@pytest.mark.asyncio
async def test_denied_rag_and_rag_chosen_is_asked_again():
    """沒放行 RAG、投機那次偏偏選了 get_rag_answer：丟掉、用實際的工具集再問。"""
    llm = _LLM(_call("get_rag_answer", query="法國國歌"), AIMessage(content="這不是健康問題"))

    result = await _agent(llm, guardrail=_Guardrail(allow_rag=False)).invoke(
        user_input="法國國歌是什麼"
    )

    assert result["response"] == "這不是健康問題"
    assert len(llm.started) == 2
    assert "get_rag_answer" in llm.bound[0]
    assert "get_rag_answer" not in llm.bound[1], "重問時綁的是沒有 RAG 的工具集"


@pytest.mark.asyncio
async def test_emergency_discards_the_speculative_call():
    """急迫度判緊急：紅卡直接出，先問的那次被取消、結果不用。"""
    llm = _LLM(AIMessage(content="一般回覆"))
    llm.delay = 0.2
    urgency = _Urgency(UrgencyVerdict(level=URGENCY_EMERGENCY, display="失去意識"), delay=0.01)

    result = await asyncio.wait_for(
        _agent(llm, guardrail=_Guardrail(delay=0.01), urgency=urgency).invoke(
            user_input="我阿公昏迷"
        ),
        timeout=1,
    )
    await asyncio.sleep(0)

    assert result["emergency"] is True
    assert '"altText": "請立即就醫"' in result["response"] or "請立即就醫" in result["response"]


@pytest.mark.asyncio
async def test_local_shortcut_skips_the_model_entirely():
    """本地分流有把握會走 RAG：guardrail 節點就定案，一次模型都不問。"""

    class _Router:
        high = 0.9

        def probability(self, _text):
            return 0.99

        def recognizes(self, _text):
            return True

    llm = _LLM()
    rag_answer = "根據 RAG 資訊，多喝水。"

    from unittest.mock import AsyncMock, MagicMock, patch

    svc = MagicMock()
    svc.answer = AsyncMock(return_value=rag_answer)
    with patch("app.tools.rag_tools._rag_answer_service", svc):
        result = await _agent(
            llm, guardrail=_Guardrail(allow_rag=True), rag_router=_Router()
        ).invoke(user_input="感冒要怎麼辦")

    assert llm.started == [], "捷徑成立就不該問模型"
    assert "多喝水" in result["response"]


@pytest.mark.asyncio
async def test_speculative_failure_falls_back_to_a_normal_call():
    class _Flaky(_LLM):
        async def ainvoke(self, messages):
            if not self.started:
                self.started.append(0.0)
                raise RuntimeError("boom")
            return await super().ainvoke(messages)

    llm = _Flaky(_call("get_family_directory"))

    result = await _agent(llm, guardrail=_Guardrail(allow_rag=True)).invoke(
        user_input="我家人有誰"
    )

    assert result["response"]
    assert len(llm.started) == 2
