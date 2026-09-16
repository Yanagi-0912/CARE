"""LINE 對話裡的走失求救：說了「我走丟了」之後長輩看到什麼、家人何時被通知。"""

import asyncio
import json
from datetime import datetime

from linebot.v3.webhooks import (
    AudioMessageContent,
    DeliveryContext,
    LocationMessageContent,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.services.line_messaging.handler.location_handler import LineLocationHandler
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.lost.lost_location_service import LostLocationService
from tests.unit.services.lost.lost_fakes import (
    DAUGHTER,
    ELDER,
    Clock,
    FakeAuthorization,
    FakeLostRepository,
    FakeProfiles,
    FakeReplier,
)


class RecordingAgent:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": "主回覆內容"}


class RecordingHistory:
    def __init__(self):
        self.saved = []

    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        self.saved.append(kwargs)


def _event(message) -> MessageEvent:
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZLOST00000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=ELDER),
        message=message,
    )


def _text(text):
    return _event(TextMessageContent(id="M1", text=text, quoteToken="qt"))


def _setup(handler_cls=LineMessageHandler, recipients=(DAUGHTER,)):
    replier = FakeReplier()
    profiles = FakeProfiles()
    service = LostLocationService(
        replier=replier,
        authorization_service=FakeAuthorization(recipients=recipients),
        user_profile_service=profiles,
        repository=FakeLostRepository(),
        liff_id="1234-abcd",
        clock=Clock(),
    )
    agent, history = RecordingAgent(), RecordingHistory()
    handler = handler_cls(
        agent=agent,
        history_service=history,
        user_profile_service=profiles,
        replier=replier,
        lost_location_service=service,
    )
    return handler, service, agent, history, replier


async def _drain(handler):
    tasks = list(handler._safety_alert_tasks)
    if tasks:
        await asyncio.gather(*tasks)


def _card_json(entry) -> str:
    return json.dumps(entry["flex"].contents.to_dict(), ensure_ascii=False)


async def test_lost_message_replies_with_the_share_card_and_notifies_family_in_background():
    handler, service, agent, history, replier = _setup()

    await handler.handle(_text("我迷路了"))

    assert agent.calls == []
    assert history.saved == []
    [reply] = replier.replied_flex
    assert "https://liff.line.me/1234-abcd/lost/share" in _card_json(reply)
    assert "別擔心，正在通知你的家人" in _card_json(reply)
    # 聊天室下方的退路：傳送一次位置
    assert reply["flex"].quick_reply.items[0].action.type == "location"

    await _drain(handler)
    assert len(replier.flex_to(DAUGHTER)) == 1
    assert replier.texts_to(ELDER) == [
        "你的家人已經收到通知了。請留在原地，按上面的按鈕讓家人看到你在哪裡。"
    ]


async def test_voice_message_saying_lost_is_caught_too():
    handler, service, agent, history, replier = _setup()

    # 語音辨識完的文字從 _process_and_reply 進來，message_type 是 audio
    await handler._process_and_reply(
        _event(AudioMessageContent(id="A1", duration=2000, contentProvider={"type": "line"})),
        "我不知道我在哪裡",
        "audio",
    )

    assert agent.calls == []
    assert len(replier.replied_flex) == 1


async def test_image_text_mentioning_lost_is_not_caught():
    handler, service, agent, history, replier = _setup()

    await handler._process_and_reply(_text("x"), "我迷路了", "image")

    assert len(agent.calls) == 1
    assert replier.replied_flex == []


async def test_no_family_gets_the_call_110_card_and_nobody_is_notified():
    handler, service, agent, history, replier = _setup(recipients=())

    await handler.handle(_text("我走丟了"))
    await _drain(handler)

    [reply] = replier.replied_flex
    assert "tel:110" in _card_json(reply)
    assert replier.flex == []
    assert replier.texts == []


async def test_saying_it_twice_does_not_notify_family_twice():
    handler, service, agent, history, replier = _setup()
    await handler.handle(_text("我走丟了"))
    await _drain(handler)

    await handler.handle(_text("我走丟了"))
    await _drain(handler)

    assert len(replier.flex_to(DAUGHTER)) == 1
    assert "家人已經收到通知，正在找你" in _card_json(replier.replied_flex[1])


async def test_other_messages_still_go_to_the_agent():
    handler, service, agent, history, replier = _setup()

    await handler.handle(_text("我媽走丟了怎麼辦"))

    assert len(agent.calls) == 1
    assert replier.replied_flex == []


def _location_event(lat=25.033, lng=121.565):
    return _event(
        LocationMessageContent(
            id="L1", type="location", title=None, address=None, latitude=lat, longitude=lng
        )
    )


async def test_location_during_a_lost_session_goes_to_family_not_to_hospital_search():
    handler, service, agent, history, replier = _setup(handler_cls=LineLocationHandler)
    await service.report(ELDER, "我走丟了", "lost")

    await handler.handle(_location_event())
    await _drain(handler)

    assert agent.calls == []
    assert replier.replies[0]["message_text"].startswith("已經把你的位置傳給家人了")
    session = await service.active(ELDER)
    assert session["last_location"]["source"] == "line"
    # 第一次收到位置：家人收到「已經收到位置」
    [card] = replier.flex_to(DAUGHTER)
    assert card.alt_text == "已經收到王阿公的位置"


async def test_location_without_a_lost_session_still_searches_hospitals():
    handler, service, agent, history, replier = _setup(handler_cls=LineLocationHandler)

    await handler.handle(_location_event())

    assert len(agent.calls) == 1
