"""
端到端路由：使用者訊息 → graph → 回覆內容。

為什麼一定要有這一層測試：
    前一版的急迫度檢查有完整的單元測試而且全綠，線上卻完全沒有作用——因為
    檢查放在一個工具裡，而 agent 從來沒有選擇呼叫那個工具。單元測試測的是
    「檢查函式判斷得對不對」，沒有任何測試測「檢查到底有沒有被執行到」。
    這個檔案補的就是後者：從 graph 入口打進去，看真正送出去的是什麼。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from linebot.v3.messaging import FlexContainer

from app.services.agent.agent import Agent
from app.services.medical.symptom_classification.urgency import (
    NOT_URGENT,
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)


class _FakeLLM:
    """記錄自己有沒有被呼叫過——短路是否真的生效就看這個。"""

    def __init__(self, reply="一般回覆"):
        self.reply = reply
        self.invocations = []

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        return AIMessage(content=self.reply)


class _FakeGuardrail:
    def __init__(self, allow_rag=False):
        self._allow_rag = allow_rag
        self.calls = []

    async def allow_rag_tool(self, text):
        self.calls.append(text)
        return self._allow_rag


class _FakeUrgency:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    async def classify(self, text, *, language="繁體中文"):
        self.calls.append((text, language))
        return self._verdict


def _emergency(display="你提到有人失去意識、叫不醒"):
    return UrgencyVerdict(level=URGENCY_EMERGENCY, display=display)


def _agent(verdict, *, allow_rag=False, llm=None):
    return Agent(
        llm=llm or _FakeLLM(),
        guardrail_service=_FakeGuardrail(allow_rag=allow_rag),
        urgency_classifier=_FakeUrgency(verdict),
    )


# --- 短路：緊急時不進 agent ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_input",
    [
        "我阿公昏迷",
        "我阿公昏迷要掛哪一科",
        "我剛剛被車撞，現在流好多血",
        "我去吃飯食物中毒",
        "grandpa is unconscious",
    ],
)
async def test_emergency_short_circuits_regardless_of_intent(user_input):
    """
    急迫度與掛號意圖正交：有沒有問「要掛哪一科」都不影響攔截。
    前一版正是因為把兩者串起來，「我阿公昏迷」才會直接掉進 RAG。
    """
    llm = _FakeLLM()
    agent = _agent(_emergency(), allow_rag=True, llm=llm)

    result = await agent.invoke(user_input=user_input)

    payload = json.loads(result["response"])
    assert payload["type"] == "flex"
    assert payload["altText"] == "請立即就醫"
    # agent 節點完全沒有被執行——沒跑 LLM、沒跑 RAG、沒呼叫任何工具。
    assert llm.invocations == []


@pytest.mark.asyncio
async def test_emergency_card_is_valid_flex_json():
    """
    回歸測試：這條路徑送出的是要原樣交給 LINE 的 Flex JSON。任何在後面接字串
    的後置處理都會讓它不再是合法 JSON，reply 端解析失敗後會把整包 JSON 當
    純文字送出——那個 bug 發生過。
    """
    result = await _agent(_emergency(), allow_rag=True).invoke(user_input="我阿公昏迷")

    payload = json.loads(result["response"])
    FlexContainer.from_json(json.dumps(payload["contents"], ensure_ascii=False))
    assert "參考資料來源" not in result["response"]


@pytest.mark.asyncio
async def test_emergency_card_carries_the_classifier_display():
    verdict = _emergency("你提到有人大量出血")
    result = await _agent(verdict).invoke(user_input="我剛剛被車撞，現在流好多血")
    assert "你提到有人大量出血" in result["response"]


@pytest.mark.asyncio
async def test_emergency_card_offers_119():
    result = await _agent(_emergency()).invoke(user_input="我阿公昏迷")
    assert "tel:119" in result["response"]


@pytest.mark.asyncio
async def test_emergency_does_not_request_location():
    """緊急卡不該再問位置——那是多一個步驟擋在求助前面。"""
    result = await _agent(_emergency()).invoke(user_input="我阿公昏迷")
    assert result["call_request_location"] is False


# --- 不緊急時一般流程完全不受影響 ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_input",
    ["中風前兆有哪些", "食物中毒可以吃什麼", "我肚子痛要掛哪一科", "你好"],
)
async def test_non_emergency_still_reaches_the_agent(user_input):
    """
    判準是「是否正在發生」，所以知識性問句本來就會被判成不緊急，RAG 不會被
    吃掉。這是關鍵字版做不到、因而必須在攔截與 RAG 之間二選一的地方。
    """
    # allow_rag=False：這裡要斷言的是「有沒有走到 agent」，不是 RAG 的行為。
    # 開著會讓 force_rag 真的去執行 get_rag_answer，把外部服務拖進單元測試。
    llm = _FakeLLM()
    agent = _agent(NOT_URGENT, allow_rag=False, llm=llm)

    result = await agent.invoke(user_input=user_input)

    assert llm.invocations, "不緊急的訊息必須照常進入 agent"
    assert result["response"] == "一般回覆"


# --- 判斷器的呼叫方式 --------------------------------------------------------


@pytest.mark.asyncio
async def test_classifier_receives_the_latest_user_message():
    classifier = _FakeUrgency(NOT_URGENT)
    agent = Agent(
        llm=_FakeLLM(),
        guardrail_service=_FakeGuardrail(),
        urgency_classifier=classifier,
    )

    await agent.invoke(user_input="我阿公昏迷")

    assert classifier.calls
    assert classifier.calls[0][0] == "我阿公昏迷"


@pytest.mark.asyncio
async def test_classifier_runs_even_when_rag_is_disallowed():
    """
    guardrail 說「與健康無關」時仍要判急迫度。兩個判斷彼此獨立，用 guardrail
    的結果去 gate 安全檢查，等於又造出一個「檢查可能不執行」的分支。
    """
    classifier = _FakeUrgency(_emergency())
    agent = Agent(
        llm=_FakeLLM(),
        guardrail_service=_FakeGuardrail(allow_rag=False),
        urgency_classifier=classifier,
    )

    result = await agent.invoke(user_input="我阿公昏迷")

    assert classifier.calls
    assert json.loads(result["response"])["altText"] == "請立即就醫"


@pytest.mark.asyncio
async def test_missing_classifier_degrades_to_normal_flow():
    """未注入判斷器時等同永遠不緊急，不得讓整條流程炸掉。"""
    llm = _FakeLLM()
    agent = Agent(llm=llm, guardrail_service=_FakeGuardrail())

    result = await agent.invoke(user_input="我阿公昏迷")

    assert result["response"] == "一般回覆"
    assert llm.invocations
