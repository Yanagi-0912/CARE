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

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return True


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
