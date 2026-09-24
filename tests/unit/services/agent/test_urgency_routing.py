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
from app.services.line_messaging.reply.reply import LineReplier
from app.services.medical.symptom_classification.urgency import (
    NOT_URGENT,
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)


@pytest.fixture(autouse=True)
def _isolated_rag_tool():
    """把 get_rag_answer 的模組全域固定成假服務。

    模型沒選任何工具時 agent 會強制轉 RAG（nodes._can_send_original_text_to_rag），
    所以這個檔案的測試會真的執行 get_rag_answer 這支工具。那支工具讀的是
    app.tools.rag_tools 的模組全域 `_rag_answer_service`：本機沒人設過它，是 None，
    工具回一句「未初始化」就結束；CI 上卻會被先跑的測試留下的真實服務污染，於是
    測試真的去連 pgvector，報 Missing PGVECTOR_DSN（2026-09-23 的 CI 紅燈）。
    這裡固定成假服務，兩邊行為一致，斷言的是流程而不是環境。
    """
    import app.tools.rag_tools as rag_tools

    class _FakeRagService:
        async def answer(self, query: str) -> str:
            return "知識庫回覆"

    previous = rag_tools._rag_answer_service
    rag_tools.configure_rag_tool(_FakeRagService())
    yield
    rag_tools.configure_rag_tool(previous)


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
async def test_emergency_card_carries_risk_alert_for_summary_but_line_still_parses_it():
    """摘要靠 riskAlert 認出紅卡；這個 key 不能讓送往 LINE 的卡片解析失敗。"""
    verdict = _emergency("你表達想結束生命")
    result = await _agent(verdict).invoke(user_input="我要自殺")

    assert json.loads(result["response"])["riskAlert"] == {"reason": "你表達想結束生命"}
    card, _speech = LineReplier._try_parse_flex_message(result["response"])
    assert card is not None
    assert "riskAlert" not in json.dumps(card.to_dict(), ensure_ascii=False)


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


# --- 緊急短路不等 guardrail（2026-09-16） --------------------------------------

import asyncio
import logging


class _HangingGuardrail:
    """永遠回不來的 guardrail：模擬 Gemini 掛住。記錄自己有沒有跑完。"""

    def __init__(self):
        self.completed = False

    async def allow_rag_tool(self, _text):
        await asyncio.sleep(30)
        self.completed = True
        return True


class _DelayedGuardrail:
    def __init__(self, allow_rag=True, delay=0.05):
        self._allow_rag = allow_rag
        self._delay = delay
        self.finished = False

    async def allow_rag_tool(self, _text):
        await asyncio.sleep(self._delay)
        self.finished = True
        return self._allow_rag


@pytest.mark.asyncio
async def test_emergency_does_not_wait_for_a_hanging_guardrail():
    """
    以前用 gather 等兩邊：guardrail 的 Gemini 一掛，「我阿公昏迷」的紅卡就跟著
    卡住，而 guardrail 的結果對緊急卡完全用不到。現在急迫度一判緊急就短路，
    guardrail 被取消。
    """
    guardrail = _HangingGuardrail()
    agent = Agent(
        llm=_FakeLLM(),
        guardrail_service=guardrail,
        urgency_classifier=_FakeUrgency(_emergency()),
    )

    # 以前這裡會等滿 30 秒；wait_for 的 2 秒是「有沒有被拖住」的判準。
    result = await asyncio.wait_for(agent.invoke(user_input="我阿公昏迷"), timeout=2)
    # 讓取消真正送達被放掉的任務（急迫度判斷若沒讓出事件迴圈，任務會在
    # 起跑前就被取消——那更好，不必特別區分）。
    await asyncio.sleep(0)

    assert json.loads(result["response"])["altText"] == "請立即就醫"
    assert guardrail.completed is False


@pytest.mark.asyncio
async def test_non_emergency_still_waits_for_guardrail():
    """不緊急時 guardrail 的結果決定 RAG 掛不掛，必須等到它。"""
    guardrail = _DelayedGuardrail(allow_rag=True)
    llm = _FakeLLM()
    agent = Agent(
        llm=llm,
        guardrail_service=guardrail,
        urgency_classifier=_FakeUrgency(NOT_URGENT),
    )

    await agent.invoke(user_input="你好")

    assert guardrail.finished is True
    assert llm.invocations


@pytest.mark.asyncio
async def test_guardrail_stage_log_marks_skip_only_on_emergency(caplog):
    """緊急時 ms 只量到急迫度那段，log 要說明 guardrail 是被放掉、不是很快。"""
    with caplog.at_level(logging.INFO, logger="app.services.agent.utils.nodes"):
        await _agent(_emergency()).invoke(user_input="我阿公昏迷")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stage=guardrail ")]
    assert len(lines) == 1
    assert "guardrail_skipped=True" in lines[0]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.agent.utils.nodes"):
        await _agent(NOT_URGENT).invoke(user_input="你好")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stage=guardrail ")]
    assert len(lines) == 1
    assert "guardrail_skipped" not in lines[0]


# --- 使用者原文不進 INFO log（2026-09-16） -------------------------------------


@pytest.mark.asyncio
async def test_user_text_preview_is_logged_at_debug_not_info(caplog):
    """INFO 進 Cloud Logging 長期保存，病史與用藥不該留在那裡；DEBUG 才印。"""
    with caplog.at_level(logging.DEBUG, logger="app.services.agent.agent"):
        await _agent(NOT_URGENT).invoke(user_input="我有糖尿病和高血壓")

    records = [r for r in caplog.records if r.name == "app.services.agent.agent"]
    info_messages = [r.getMessage() for r in records if r.levelno >= logging.INFO]
    debug_messages = [r.getMessage() for r in records if r.levelno == logging.DEBUG]
    assert not any("我有糖尿病和高血壓" in m for m in info_messages)
    assert not any("user_input_preview" in m for m in info_messages)
    assert any("user_input_preview=我有糖尿病和高血壓" in m for m in debug_messages)


# --- 判定整個帶出 agent，不拆成字串（10.14）------------------------------------


@pytest.mark.asyncio
async def test_emergency_returns_the_verdict_object_for_the_followup():
    """紅卡之後的人物辨識與通報要拿到完整判定，拆成字串再組回來只會掉欄位。"""
    verdict = _emergency("你提到有人跌倒、叫不醒")

    result = await _agent(verdict).invoke(user_input="我阿公跌倒叫不醒")

    assert result["emergency"] is True
    assert result["urgency_verdict"] is verdict


@pytest.mark.asyncio
async def test_non_emergency_returns_no_verdict():
    result = await _agent(NOT_URGENT).invoke(user_input="我肚子痛要掛哪一科")

    assert result["emergency"] is False
    assert result["urgency_verdict"] is None


@pytest.mark.asyncio
async def test_emergency_card_never_waits_for_people():
    """紅卡只用判定組卡：判斷器的 identify_affected 在 agent 裡一次都不被呼叫。"""

    class _Classifier(_FakeUrgency):
        def __init__(self, verdict):
            super().__init__(verdict)
            self.identify_calls = 0

        async def identify_affected(self, *args, **kwargs):
            self.identify_calls += 1
            raise AssertionError("紅卡送出前不得辨識人物")

    classifier = _Classifier(_emergency())
    agent = Agent(
        llm=_FakeLLM(),
        guardrail_service=_FakeGuardrail(),
        urgency_classifier=classifier,
    )

    result = await agent.invoke(user_input="我阿公昏迷")

    assert json.loads(result["response"])["type"] == "flex"
    assert classifier.identify_calls == 0
