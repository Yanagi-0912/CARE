"""走失判斷的三段：關鍵字／分類器有把握直接通報、沒把握照常回答並附「我迷路了」按鈕、其餘照常進 agent。"""

import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    PostbackContent,
    PostbackEvent,
    TextMessageContent,
    UserSource,
)

from app.services.medical.symptom_classification.urgency import (
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)
from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.lost.lost_classifier import LostIntentDetector
from app.services.lost.lost_location_service import LostLocationService
from resources.flex_messages.lost_location_flex_message import (
    LOST_CONFIRM_ACTION,
    lost_confirm_postback_data,
)
from tests.unit.services.lost.lost_fakes import (
    DAUGHTER,
    ELDER,
    Clock,
    FakeAuthorization,
    FakeLostRepository,
    FakeProfiles,
    FakeReplier,
)


class FakeClassifier:
    """貼齊 LocalGuardrailClassifier 用到的介面：probability() 與 low／high。"""

    low = 0.2
    high = 0.8

    def __init__(self, probability=0.0, error=None):
        self._probability = probability
        self._error = error
        self.calls = []

    def probability(self, text):
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return self._probability


# ── 判斷器 ────────────────────────────────────────────────────────────


def test_keyword_hit_does_not_need_the_classifier():
    classifier = FakeClassifier(probability=0.0)

    detection = LostIntentDetector(classifier).detect("我迷路了")

    assert detection.intent == "lost"
    assert detection.source == "keyword"
    assert not detection.needs_confirmation
    assert classifier.calls == []


@pytest.mark.parametrize(
    ("probability", "intent", "confirm"),
    [
        (0.95, "lost", False),
        (0.8, "lost", False),
        (0.5, "lost", True),
        (0.2, "lost", True),
        (0.1, None, False),
    ],
)
def test_classifier_zones(probability, intent, confirm):
    detection = LostIntentDetector(FakeClassifier(probability)).detect(
        "I walked out of the market and nothing here looks familiar"
    )

    assert detection.intent == intent
    assert detection.needs_confirmation is confirm


def test_classifier_error_is_treated_as_ordinary_message():
    detection = LostIntentDetector(FakeClassifier(error=RuntimeError("boom"))).detect(
        "Saya keluar dari pasar dan tidak kenal jalan ini"
    )

    assert detection.intent is None


def test_missing_model_file_falls_back_to_keywords(tmp_path):
    detector = LostIntentDetector.load(tmp_path / "nope.json")

    assert detector.detect("我迷路了").intent == "lost"
    assert detector.detect("I walked out of the market and nothing here looks familiar").intent is None


def test_real_model_file_loads_and_catches_other_languages():
    detector = LostIntentDetector.load()

    for text in ["I'm lost and I don't know where I am", "Saya tersesat, tidak tahu ada di mana"]:
        assert detector.detect(text).intent == "lost", text
    assert detector.detect("高血壓可以吃香蕉嗎").intent is None


# ── postback data ─────────────────────────────────────────────────────


def test_confirm_postback_carries_the_words_within_lines_limit():
    words = "我不知道我在哪 & 旁邊有 7-11 = 很多人 %" + "很" * 400

    data = lost_confirm_postback_data(words)
    params = parse_qs(data)

    assert len(data) <= 300
    assert params["action"] == [LOST_CONFIRM_ACTION]
    assert params["w"][0].startswith("我不知道我在哪 旁邊有 7-11 很多人")


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_help_button_texts_exist_and_label_fits(language):
    assert _MESSAGES["lost.help.display"][language].strip()
    assert 0 < len(_MESSAGES["lost.help.quick_reply"][language]) <= 20


# ── LINE 流程 ─────────────────────────────────────────────────────────


class RecordingAgent:
    def __init__(self, response=None):
        self.calls = []
        self._response = response or {"response": "主回覆內容"}

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self._response)


class History:
    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        pass


class FakeUrgency:
    def __init__(self, emergency=False):
        self.emergency = emergency
        self.calls = []

    async def classify(self, text, *, language="zh-TW"):
        from app.services.medical.symptom_classification.urgency import (
            NOT_URGENT,
            URGENCY_EMERGENCY,
            UrgencyVerdict,
        )

        self.calls.append((text, language))
        if self.emergency:
            return UrgencyVerdict(level=URGENCY_EMERGENCY, display="叫不醒、身體冰冷")
        return NOT_URGENT

    async def identify_affected(self, verdict, text, *, language="zh-TW"):
        # 紅卡之後的人物辨識；走失是長輩自己回報，這裡不補人物。
        return verdict


class FakeEmergencyAlert:
    def __init__(self):
        self.calls = []

    async def notify(self, user_id, reason, patient_words=""):
        self.calls.append((user_id, reason, patient_words))
        return True


def _setup(classifier, *, urgency=None, agent_response=None):
    replier = FakeReplier()
    profiles = FakeProfiles()
    service = LostLocationService(
        replier=replier,
        authorization_service=FakeAuthorization(recipients=[DAUGHTER]),
        user_profile_service=profiles,
        repository=FakeLostRepository(),
        liff_id="1234-abcd",
        clock=Clock(),
        intent_detector=LostIntentDetector(classifier),
    )
    agent = RecordingAgent(agent_response)
    emergency_alert = FakeEmergencyAlert()
    handler = LineMessageHandler(
        agent=agent,
        history_service=History(),
        user_profile_service=profiles,
        replier=replier,
        lost_location_service=service,
        urgency_classifier=urgency,
        emergency_family_alert_service=emergency_alert,
    )
    dispatcher = LineEventDispatcher(
        message_handler=handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=replier,
    )
    return dispatcher, handler, service, agent, replier, emergency_alert


def _text(text):
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZLOSTCLS0000000000000A",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=ELDER),
        message=TextMessageContent(id="M1", text=text, quoteToken="qt"),
    )


def _postback(data):
    return PostbackEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZLOSTCLS0000000000000B",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt2",
        source=UserSource(type="user", userId=ELDER),
        postback=PostbackContent(data=data),
    )


async def _drain(handler):
    while handler._safety_alert_tasks:
        await asyncio.gather(*list(handler._safety_alert_tasks))


async def test_confident_classifier_starts_the_flow_without_the_agent():
    dispatcher, handler, service, agent, replier, _ = _setup(FakeClassifier(0.95))

    await dispatcher.handle(_text("I walked out of the market and nothing here looks familiar"))
    await _drain(handler)

    assert agent.calls == []
    assert await service.active(ELDER) is not None
    assert len(replier.flex_to(DAUGHTER)) == 1


async def test_unsure_classifier_still_answers_and_offers_the_lost_button():
    dispatcher, handler, service, agent, replier, _ = _setup(FakeClassifier(0.5))

    await dispatcher.handle(_text("這邊都是田，我找不太到剛剛那條路"))

    # 不攔：照常進 agent、照常回答
    assert len(agent.calls) == 1
    assert await service.active(ELDER) is None
    [reply] = replier.replies
    assert reply["message_text"] == "主回覆內容"
    data = reply["lost_help_postback"]
    assert parse_qs(data)["action"] == [LOST_CONFIRM_ACTION]

    # 長輩按了按鈕：啟動求救，家人收到的通報帶著原話
    await dispatcher.handle(_postback(data))
    await _drain(handler)

    session = await service.active(ELDER)
    assert session["patient_words"] == "這邊都是田，我找不太到剛剛那條路"
    [family_card] = replier.flex_to(DAUGHTER)
    assert "這邊都是田，我找不太到剛剛那條路" in json.dumps(
        family_card.contents.to_dict(), ensure_ascii=False
    )


async def test_unsure_message_that_turns_out_to_be_an_emergency_gets_no_lost_button():
    dispatcher, handler, service, agent, replier, _ = _setup(
        FakeClassifier(0.5),
        agent_response={"response": "{}", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="叫不醒")},
    )

    await dispatcher.handle(_text("阮後生叫袂醒"))

    assert replier.replies[0]["lost_help_postback"] is None


async def test_unlikely_goes_to_the_agent_without_a_button():
    dispatcher, handler, service, agent, replier, _ = _setup(FakeClassifier(0.05))

    await dispatcher.handle(_text("高血壓可以吃香蕉嗎"))

    assert len(agent.calls) == 1
    assert replier.replies[0]["lost_help_postback"] is None


async def test_lost_flow_still_sends_the_red_card_when_it_is_an_emergency():
    urgency = FakeUrgency(emergency=True)
    dispatcher, handler, service, agent, replier, emergency_alert = _setup(
        FakeClassifier(0.95), urgency=urgency
    )

    await dispatcher.handle(_text("阮後生不知按怎叫袂醒身軀冷冰冰"))
    await _drain(handler)

    assert agent.calls == []
    assert urgency.calls == [("阮後生不知按怎叫袂醒身軀冷冰冰", "zh-TW")]
    # 定位卡照常先回，紅卡在背景補推給長輩，急症通報照常送出
    assert len(replier.replied_flex) == 1
    red_cards = replier.flex_to(ELDER)
    assert len(red_cards) == 1
    assert emergency_alert.calls == [(ELDER, "叫不醒、身體冰冷", "阮後生不知按怎叫袂醒身軀冷冰冰")]


async def test_lost_flow_without_emergency_sends_no_red_card():
    urgency = FakeUrgency(emergency=False)
    dispatcher, handler, service, agent, replier, emergency_alert = _setup(
        FakeClassifier(0.95), urgency=urgency
    )

    await dispatcher.handle(_text("我迷路了"))
    await _drain(handler)

    assert len(urgency.calls) == 1
    assert replier.flex_to(ELDER) == []
    assert emergency_alert.calls == []
