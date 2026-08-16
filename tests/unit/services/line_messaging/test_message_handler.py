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


def _handler(safety_alert_service=None, replier=None):
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
        agent=_Agent(),
        history_service=_History(),
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
