import asyncio
from datetime import datetime

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.services.line_messaging.handler.message_handler import LineMessageHandler

USER_ID = "U_PATIENT"
USER_TEXT = "朋友從日本帶回來的合利他命強効錠 EX PLUS 可以吃嗎"


class FakeSafetyAlertService:
    def __init__(self, error=None):
        self._error = error
        self.calls = []

    async def check(self, user_id, text):
        self.calls.append((user_id, text))
        if self._error is not None:
            raise self._error


class FakeReplier:
    def __init__(self):
        self.replies = []
        self.pushed_texts = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return True

    async def push_text(self, user_id, text):
        self.pushed_texts.append((user_id, text))


def _text_event() -> MessageEvent:
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=USER_ID),
        message=TextMessageContent(id="M1", text=USER_TEXT, quoteToken="qt"),
    )


def _handler(safety_alert_service=None, replier=None, agent=None, history_service=None):
    class _Agent:
        async def invoke(self, **kwargs):
            return {"response": "主回覆內容"}

    class _History:
        def __init__(self):
            self.saved = []

        async def load_history(self, **kwargs):
            return []

        async def save_turn(self, **kwargs):
            self.saved.append(kwargs)

    class _Profile:
        async def get_user_profile(self, user_id):
            return {"settings": {"language": "zh-TW", "voice_reply_enabled": False}}

    return LineMessageHandler(
        agent=agent or _Agent(),
        history_service=history_service or _History(),
        user_profile_service=_Profile(),
        replier=replier or FakeReplier(),
        safety_alert_service=safety_alert_service,
    )


async def _drain(handler):
    """等併行的評估任務跑完。任務被持有參考，才有得等（見 6.4）。"""
    tasks = list(handler._safety_alert_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_safety_check_runs_with_the_same_text_as_the_main_reply():
    service = FakeSafetyAlertService()
    handler = _handler(safety_alert_service=service)

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls == [(USER_ID, USER_TEXT)]


async def test_no_safety_check_without_a_service():
    """開關關閉時 dependencies 不會組出這個服務，handler 端就完全不動作。"""
    replier = FakeReplier()
    handler = _handler(safety_alert_service=None, replier=replier)

    await handler.handle(_text_event())

    assert handler._safety_alert_tasks == set()
    assert replier.replies[0]["message_text"] == "主回覆內容"


async def test_main_reply_is_unchanged_when_the_check_is_enabled():
    replier = FakeReplier()
    handler = _handler(safety_alert_service=FakeSafetyAlertService(), replier=replier)

    await handler.handle(_text_event())
    await _drain(handler)

    assert len(replier.replies) == 1
    assert replier.replies[0]["message_text"] == "主回覆內容"
    assert replier.replies[0]["user_id"] == USER_ID


async def test_main_reply_survives_a_failing_safety_check():
    replier = FakeReplier()
    handler = _handler(
        safety_alert_service=FakeSafetyAlertService(error=RuntimeError("boom")),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.replies[0]["message_text"] == "主回覆內容"


async def test_safety_task_reference_is_released_when_done():
    """任務要被持有到完成，否則可能被 GC 回收；完成後也不能一直累積。"""
    handler = _handler(safety_alert_service=FakeSafetyAlertService())

    await handler.handle(_text_event())
    await _drain(handler)
    await asyncio.sleep(0)

    assert handler._safety_alert_tasks == set()


async def test_safety_check_does_not_delay_the_main_reply():
    """評估要與主回覆併行：主回覆送出時，評估還沒跑完也無所謂。"""
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowService:
        def __init__(self):
            self.calls = []

        async def check(self, user_id, text):
            self.calls.append((user_id, text))
            started.set()
            await release.wait()

    service = SlowService()
    replier = FakeReplier()
    handler = _handler(safety_alert_service=service, replier=replier)

    await handler.handle(_text_event())

    assert replier.replies, "主回覆必須在評估完成之前就送出"
    release.set()
    await _drain(handler)
    assert service.calls == [(USER_ID, USER_TEXT)]


class RecordingAgent:
    """可指定回傳值的 agent；同時記下呼叫當下看到的 ContextVar 狀態。"""

    def __init__(self, response="蜂蜜放室溫即可 [1]。", answer_kind="rag"):
        self._response = response
        self._answer_kind = answer_kind
        self.seen_sources = None

    async def invoke(self, **kwargs):
        from app.core.rag_sources import get_request_rag_sources

        self.seen_sources = get_request_rag_sources()
        return {
            "response": self._response,
            "call_request_location": False,
            "answer_kind": self._answer_kind,
        }


class RecordingHistory:
    def __init__(self):
        self.saved = []

    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        self.saved.append(kwargs)


@pytest.mark.asyncio
async def test_handler_passes_answer_kind_and_question_to_replier():
    """呈現層要靠這兩個值才組得出卡片。"""
    replier = FakeReplier()
    handler = _handler(replier=replier, agent=RecordingAgent())

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.replies[0]["answer_kind"] == "rag"
    assert replier.replies[0]["user_question"] == USER_TEXT


@pytest.mark.asyncio
async def test_handler_saves_plain_text_to_history_not_flex_json():
    """卡片在呈現層才組，因此存進歷史的必須仍是純文字。

    這正是不走 medical_tool_names 白名單的理由之一：那條路徑會把整包
    Flex JSON 存成 ai_reply，下一輪 agent 讀到自己上一則回覆是一大坨 JSON。
    """
    history = RecordingHistory()
    handler = _handler(agent=RecordingAgent(), history_service=history)

    await handler.handle(_text_event())
    await _drain(handler)

    saved = history.saved[0]["ai_reply"]
    assert saved == "蜂蜜放室溫即可 [1]。"
    assert not saved.strip().startswith("{")


@pytest.mark.asyncio
async def test_handler_clears_rag_sources_between_turns():
    """上一輪的來源不得殘留成這一輪的按鈕。"""
    from app.core.rag_sources import (
        SourceRef,
        begin_request_rag_sources,
        get_request_rag_sources,
        reset_request_rag_sources,
        set_request_rag_sources,
    )

    leaked = begin_request_rag_sources()
    try:
        set_request_rag_sources(
            [SourceRef(index=1, label="上一輪的來源", url="https://example.com/stale")]
        )
        agent = RecordingAgent(answer_kind=None)
        handler = _handler(agent=agent)

        await handler.handle(_text_event())
        await _drain(handler)

        assert agent.seen_sources == (), "進入 agent 前來源必須已清空"
        # 斷言必須在還原 leaked token 之前：handler 的 finally 應該把
        # ContextVar 還原成它進來時的值，也就是這裡設的 leaked。
        assert (
            get_request_rag_sources()[0].label == "上一輪的來源"
        ), "handler 必須還原 ContextVar，而不是留在清空狀態"
    finally:
        reset_request_rag_sources(leaked)

    assert get_request_rag_sources() == ()


# --- request-scoped 使用者設定 -----------------------------------------------
#
# 語言、字級、年齡三者都靠 ContextVar 傳給拿不到 user_profile 的地方
# （LangChain tool、Flex 組裝）。它們的共同失效模式是「import 還在、呼叫沒了」：
# 不會拋錯、不會有日誌，只是所有使用者都退回預設值。
#
# 年齡這一條就是這樣掉過一次——main 合併時 handler 的呼叫被覆蓋掉，只剩 import，
# 於是症狀科別建議的兒科過濾永遠當成人處理。現有的科別測試都直接注入年齡，
# 沒有一條走 handler，所以全綠。這裡補的就是那條路徑。


def _profile_handler(user_profile, agent):
    class _History:
        async def load_history(self, **kwargs):
            return []

        async def save_turn(self, **kwargs):
            pass

    class _Profile:
        async def get_user_profile(self, user_id):
            return user_profile

    return LineMessageHandler(
        agent=agent,
        history_service=_History(),
        user_profile_service=_Profile(),
        replier=FakeReplier(),
        safety_alert_service=None,
    )


class _AgentCapturingContext:
    """在 agent 執行的當下取樣 ContextVar——那正是 tool 會讀到的時機。"""

    def __init__(self):
        self.seen = {}

    async def invoke(self, **kwargs):
        from app.core.user_age import get_request_age
        from app.core.user_font_size import get_request_font_size
        from app.core.user_language import get_request_language

        self.seen = {
            "age": get_request_age(),
            "font_size": get_request_font_size(),
            "language": get_request_language(),
        }
        return {"response": "主回覆內容"}


@pytest.mark.parametrize("age", [8, 40, 70])
async def test_user_age_reaches_the_agent(age):
    agent = _AgentCapturingContext()
    handler = _profile_handler(
        {"age": age, "settings": {"language": "zh-TW", "font_size": "large"}}, agent
    )

    await handler.handle(_text_event())

    assert agent.seen["age"] == age


async def test_missing_age_is_none_not_a_default_number():
    """
    拿不到年齡時要表現為「不知道」，由呼叫端決定保守或寬鬆。給一個假的預設值
    會讓兒科過濾以為自己知道使用者幾歲。
    """
    agent = _AgentCapturingContext()
    handler = _profile_handler({"settings": {"language": "zh-TW"}}, agent)

    await handler.handle(_text_event())

    assert agent.seen["age"] is None


async def test_language_and_font_size_reach_the_agent_too():
    """三個 ContextVar 是同一套機制，一起釘住，少接一個就會失敗。"""
    agent = _AgentCapturingContext()
    handler = _profile_handler(
        {"age": 30, "settings": {"language": "en", "font_size": "xlarge"}}, agent
    )

    await handler.handle(_text_event())

    assert agent.seen["language"] == "en"
    assert agent.seen["font_size"] == "xlarge"


async def test_age_is_reset_after_the_turn():
    """不還原會讓下一位使用者沿用上一位的年齡。"""
    from app.core.user_age import get_request_age

    agent = _AgentCapturingContext()
    handler = _profile_handler({"age": 8, "settings": {}}, agent)

    await handler.handle(_text_event())

    assert agent.seen["age"] == 8
    assert get_request_age() is None


# --- 緊急狀況家人通報的排程 ---------------------------------------------------


class FakeEmergencyAlertService:
    def __init__(self, sent=True):
        self.calls = []
        self._sent = sent

    async def notify(self, user_id, reason, patient_words=""):
        self.calls.append((user_id, reason, patient_words))
        return self._sent


class _AgentReturning:
    def __init__(self, payload):
        self._payload = payload

    async def invoke(self, **kwargs):
        return self._payload


def _emergency_handler(agent_payload, alert_service, replier=None):
    class _History:
        async def load_history(self, **kwargs):
            return []

        async def save_turn(self, **kwargs):
            pass

    class _Profile:
        async def get_user_profile(self, user_id):
            return {"settings": {"language": "zh-TW"}}

    return LineMessageHandler(
        agent=_AgentReturning(agent_payload),
        history_service=_History(),
        user_profile_service=_Profile(),
        replier=replier or FakeReplier(),
        safety_alert_service=None,
        emergency_family_alert_service=alert_service,
    )


async def test_family_alert_carries_the_users_verbatim_text():
    """
    「王小明可能需要協助」與「王小明剛剛喝了 3 瓶農藥」對家屬是完全不同的
    兩件事：後者直接決定要打 119、要告訴派遣員喝了什麼、喝了多少。原話必須
    逐字傳過去，不能只送系統的白話摘要。
    """
    service = FakeEmergencyAlertService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "提到喝下大量農藥"},
        service,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls[0][2] == USER_TEXT


async def test_emergency_verdict_schedules_a_family_alert():
    service = FakeEmergencyAlertService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "提到失去意識"},
        service,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls == [(USER_ID, "提到失去意識", USER_TEXT)]


async def test_non_emergency_does_not_notify_family():
    """誤報通知的是第三人，收不回來——沒有判定為緊急就一則都不能送。"""
    service = FakeEmergencyAlertService()
    handler = _emergency_handler({"response": "一般回覆"}, service)

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls == []


async def test_family_alert_does_not_block_the_reply():
    """
    當事人那張紅卡是最該先到的東西。查族譜與逐一推播全部串在回覆前面，
    就是讓正在出事的人多等好幾秒。
    """
    replier = FakeReplier()

    class _SlowService:
        def __init__(self):
            self.done = False

        async def notify(self, user_id, reason, patient_words=""):
            await asyncio.sleep(0.05)
            self.done = True
            return False

    service = _SlowService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "x"},
        service,
        replier=replier,
    )

    await handler.handle(_text_event())

    assert replier.replies, "回覆必須先送出"
    assert service.done is False, "通報不得擋在回覆前面"
    await _drain(handler)
    assert service.done is True


async def test_patient_is_told_only_when_someone_was_actually_notified():
    """沒有合格收件人時說「我已經讓你的家人知道」是假的。"""
    replier = FakeReplier()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "x"},
        FakeEmergencyAlertService(sent=False),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.pushed_texts == []


async def test_patient_is_told_when_family_was_notified():
    replier = FakeReplier()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "x"},
        FakeEmergencyAlertService(sent=True),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert len(replier.pushed_texts) == 1
    assert "不用一個人" in replier.pushed_texts[0][1]


async def test_alert_failure_never_reaches_the_user():
    """背景旁路，例外不得逸散，也不得影響已經送出的回覆。"""

    class _Exploding:
        async def notify(self, user_id, reason, patient_words=""):
            raise RuntimeError("boom")

    replier = FakeReplier()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "emergency_reason": "x"},
        _Exploding(),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.replies
