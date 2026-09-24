import asyncio
from datetime import datetime

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.services.medical.symptom_classification.urgency import (
    URGENCY_EMERGENCY,
    AffectedPerson,
    UrgencyVerdict,
)
from app.i18n.messages import t
from app.services.safety.emergency_alert_service import EmergencyFamilyAlertService
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


class FakeShareCardService:
    CARD = '{"type": "flex", "altText": "邀請朋友一起用 CARE", "contents": {}}'

    def __init__(self):
        self.calls = 0

    async def build_reply_text(self):
        self.calls += 1
        return self.CARD


# 名稱加 Share 前綴：這個檔案後面另有同名的 RecordingAgent／RecordingHistory，
# 模組層級後定義的會蓋掉先定義的。
class ShareRecordingAgent:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": "主回覆內容"}


class ShareRecordingHistory:
    def __init__(self):
        self.saved = []

    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        self.saved.append(kwargs)


class VoiceOnProfile:
    """開著語音回覆的使用者：分享卡仍不該唸。"""

    async def get_user_profile(self, user_id):
        return {"settings": {"language": "zh-TW", "voice_reply_enabled": True}}


def _text_event_saying(text: str) -> MessageEvent:
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000001",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=USER_ID),
        message=TextMessageContent(id="M2", text=text, quoteToken="qt"),
    )


def _share_handler(share_card_service):
    agent, history, replier = ShareRecordingAgent(), ShareRecordingHistory(), FakeReplier()
    handler = LineMessageHandler(
        agent=agent,
        history_service=history,
        user_profile_service=VoiceOnProfile(),
        replier=replier,
        share_card_service=share_card_service,
    )
    return handler, agent, history, replier


async def test_share_phrase_gets_the_card_without_going_through_the_agent():
    share = FakeShareCardService()
    handler, agent, history, replier = _share_handler(share)

    await handler.handle(_text_event_saying("加好友"))

    assert agent.calls == []
    assert share.calls == 1
    assert replier.replies[0]["message_text"] == FakeShareCardService.CARD
    # 比照貼圖：不唸語音、不寫進對話紀錄
    assert replier.replies[0]["voice_reply_enabled"] is False
    assert history.saved == []


async def test_other_messages_still_go_to_the_agent():
    share = FakeShareCardService()
    handler, agent, history, replier = _share_handler(share)

    await handler.handle(_text_event_saying("高血壓可以吃香蕉嗎"))

    assert len(agent.calls) == 1
    assert share.calls == 0
    assert replier.replies[0]["message_text"] == "主回覆內容"


async def test_share_phrase_goes_to_the_agent_when_no_share_service_is_wired():
    handler, agent, history, replier = _share_handler(None)

    await handler.handle(_text_event_saying("加好友"))

    assert len(agent.calls) == 1


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


class _Links:
    """家庭連結的替身：`links` 裡的 (回報者, 病人) 才有連結，任何角色皆可。"""

    def __init__(self, links=(), *, error=None):
        self.links = set(links)
        self.error = error
        self.calls = []

    async def resolve_role(self, operator_id, target_owner_id, now=None):
        self.calls.append((operator_id, target_owner_id))
        if self.error:
            raise self.error
        return "MEMBER" if (operator_id, target_owner_id) in self.links else None

    async def authorize(self, *args, **kwargs):
        raise AssertionError("緊急回報不得走 SENSITIVE READ 授權")


class FakeEmergencyAlertService(EmergencyFamilyAlertService):
    """選病人用正式邏輯（patients_to_notify），只把推播換成記錄。"""

    def __init__(self, sent=True, links=(), link_error=None):
        super().__init__(replier=None, authorization_service=_Links(links, error=link_error))
        self.calls = []
        self.reporters = []
        self._sent = sent

    async def notify(self, user_id, reason, patient_words="", *, reporter_id=""):
        self.calls.append((user_id, reason, patient_words))
        self.reporters.append(reporter_id)
        return self._sent


class _SelfReport:
    """紅卡之後的人物辨識替身：事件歸給發話者本人。"""

    def __init__(self):
        self.calls = []

    async def identify_affected(self, verdict, text, *, language):
        from dataclasses import replace

        self.calls.append(text)
        return replace(verdict, affected=(AffectedPerson(kind="self", event="急症"),))


class _AgentReturning:
    def __init__(self, payload):
        self._payload = payload

    async def invoke(self, **kwargs):
        return self._payload


def _emergency_handler(
    agent_payload,
    alert_service,
    replier=None,
    *,
    urgency_classifier=None,
    patient_context_service=None,
):
    # 沒指定時人物歸給發話者本人：這一段的舊測試都在驗本人急症的通報。
    urgency_classifier = urgency_classifier or _SelfReport()
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
        urgency_classifier=urgency_classifier,
        patient_context_service=patient_context_service,
    )


async def test_family_alert_carries_the_users_verbatim_text():
    """
    「王小明可能需要協助」與「王小明剛剛喝了 3 瓶農藥」對家屬是完全不同的
    兩件事：後者直接決定要打 119、要告訴派遣員喝了什麼、喝了多少。原話必須
    逐字傳過去，不能只送系統的白話摘要。
    """
    service = FakeEmergencyAlertService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="提到喝下大量農藥")},
        service,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls[0][2] == USER_TEXT


async def test_emergency_verdict_schedules_a_family_alert():
    service = FakeEmergencyAlertService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="提到失去意識")},
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

    class _SlowService(FakeEmergencyAlertService):
        def __init__(self):
            super().__init__(sent=False)
            self.done = False

        async def notify(self, user_id, reason, patient_words="", *, reporter_id=""):
            await asyncio.sleep(0.05)
            self.done = True
            return False

    service = _SlowService()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="x")},
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
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="x")},
        FakeEmergencyAlertService(sent=False),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.pushed_texts == []


async def test_patient_is_told_when_family_was_notified():
    replier = FakeReplier()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="x")},
        FakeEmergencyAlertService(sent=True),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert len(replier.pushed_texts) == 1
    assert "不用一個人" in replier.pushed_texts[0][1]


async def test_alert_failure_never_reaches_the_user():
    """背景旁路，例外不得逸散，也不得影響已經送出的回覆。"""

    class _Exploding(FakeEmergencyAlertService):
        async def notify(self, user_id, reason, patient_words="", *, reporter_id=""):
            raise RuntimeError("boom")

    replier = FakeReplier()
    handler = _emergency_handler(
        {"response": "卡片", "emergency": True, "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="x")},
        _Exploding(),
        replier=replier,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.replies


# --- 行動先到：紅卡之後才辨識人物、查族譜、通報（10.14）----------------------


from app.models.family_tree import FamilyMember  # noqa: E402
from app.services.family.person_resolution import resolve_person  # noqa: E402

_GRANDPA_FELL = UrgencyVerdict(level=URGENCY_EMERGENCY, display="你提到有人跌倒")


def _emergency_payload(verdict=_GRANDPA_FELL):
    return {"response": "紅卡", "emergency": True, "urgency_verdict": verdict}


class _Identifier:
    """急迫度判斷器的替身：只實作紅卡之後才會用到的 identify_affected。"""

    def __init__(self, *people, delay=0.0, error=None):
        self.people = people
        self.delay = delay
        self.error = error
        self.calls = []

    async def identify_affected(self, verdict, text, *, language):
        self.calls.append((text, language))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        from dataclasses import replace

        return replace(verdict, affected=self.people)


class _FamilyList:
    def __init__(self, *members, delay=0.0, error=None):
        self.members = members
        self.delay = delay
        self.error = error
        self.calls = []

    async def resolve_person(self, operator_id, *, person, relationship):
        self.calls.append(operator_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return resolve_person(self.members, person=person, relationship=relationship)


_GRANDPA = AffectedPerson(kind="family", label="阿公", relationship="grandparent", event="跌倒")


def _grandpa_member(user_id="U_GRANDPA", name="王大明"):
    return FamilyMember(user_id=user_id, display_name=name, relationship_type="grandparent")


class _OrderedReplier(FakeReplier):
    """記下紅卡、推播與通報的先後。"""

    def __init__(self, log):
        super().__init__()
        self.log = log

    async def reply(self, **kwargs):
        self.log.append("red_card")
        return await super().reply(**kwargs)

    async def push_text(self, user_id, text):
        self.log.append("push_text")
        await super().push_text(user_id, text)


class _OrderedAlert(FakeEmergencyAlertService):
    def __init__(self, log, sent=False, links=()):
        super().__init__(sent=sent, links=links)
        self.log = log

    async def notify(self, user_id, reason, patient_words="", *, reporter_id=""):
        self.log.append("notify")
        return await super().notify(
            user_id, reason, patient_words, reporter_id=reporter_id
        )


async def test_red_card_is_sent_before_identification_lookup_and_alert_start():
    log = []
    identifier = _Identifier(_GRANDPA)
    family = _FamilyList(_grandpa_member())
    handler = _emergency_handler(
        _emergency_payload(),
        _OrderedAlert(log, links={(USER_ID, "U_GRANDPA")}),
        replier=_OrderedReplier(log),
        urgency_classifier=identifier,
        patient_context_service=family,
    )

    await handler.handle(_text_event())

    assert log == ["red_card"], "紅卡送出前不得開始任何後續工作"
    assert identifier.calls == [] and family.calls == []
    await _drain(handler)
    assert log[0] == "red_card"
    assert "notify" in log


async def test_slow_family_lookup_does_not_delay_the_red_card():
    replier = FakeReplier()
    family = _FamilyList(_grandpa_member(), delay=0.05)
    handler = _emergency_handler(
        _emergency_payload(),
        None,
        replier=replier,
        urgency_classifier=_Identifier(_GRANDPA, delay=0.05),
        patient_context_service=family,
    )

    await handler.handle(_text_event())

    assert len(replier.replies) == 1
    assert replier.pushed_texts == []
    await _drain(handler)
    assert replier.pushed_texts == [(USER_ID, "請留在阿公身邊，並依紅卡立即尋求協助。")]


async def test_family_lookup_failure_still_sends_the_card_and_a_neutral_prompt():
    replier = FakeReplier()
    handler = _emergency_handler(
        _emergency_payload(),
        FakeEmergencyAlertService(sent=False),
        replier=replier,
        urgency_classifier=_Identifier(_GRANDPA),
        patient_context_service=_FamilyList(error=RuntimeError("mongo down")),
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert len(replier.replies) == 1
    assert replier.pushed_texts == [(USER_ID, "請留在對方身邊，並依紅卡立即尋求協助。")]


async def test_identification_failure_says_nothing_extra_and_keeps_the_card():
    """辨識失敗當成本人：紅卡本來就是對他說的，不補稱謂提示。"""
    replier = FakeReplier()
    handler = _emergency_handler(
        _emergency_payload(),
        None,
        replier=replier,
        urgency_classifier=_Identifier(error=RuntimeError("gemini down")),
        patient_context_service=_FamilyList(_grandpa_member()),
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert len(replier.replies) == 1
    assert replier.pushed_texts == []


async def test_two_grandparents_are_addressed_neutrally():
    replier = FakeReplier()
    handler = _emergency_handler(
        _emergency_payload(),
        None,
        replier=replier,
        urgency_classifier=_Identifier(_GRANDPA),
        patient_context_service=_FamilyList(
            _grandpa_member("U_G1", "王大明"), _grandpa_member("U_G2", "李阿土")
        ),
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.pushed_texts == [(USER_ID, "請留在對方身邊，並依紅卡立即尋求協助。")]


async def test_self_emergency_gets_no_extra_prompt():
    replier = FakeReplier()
    family = _FamilyList(_grandpa_member())
    handler = _emergency_handler(
        _emergency_payload(),
        None,
        replier=replier,
        urgency_classifier=_Identifier(AffectedPerson(kind="self", event="昏倒")),
        patient_context_service=family,
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.pushed_texts == []
    assert family.calls == []


async def test_identification_uses_the_users_text_and_language():
    identifier = _Identifier()
    handler = _emergency_handler(
        _emergency_payload(), None, urgency_classifier=identifier
    )

    await handler.handle(_text_event())
    await _drain(handler)

    assert identifier.calls == [(USER_TEXT, "zh-TW")]


async def test_emergency_without_a_verdict_object_still_alerts_family():
    """舊格式的 agent 回覆（只有 emergency=True）不得讓通報消失。"""
    service = FakeEmergencyAlertService()
    handler = _emergency_handler({"response": "紅卡", "emergency": True}, service)

    await handler.handle(_text_event())
    await _drain(handler)

    assert service.calls == [(USER_ID, "", USER_TEXT)]


# --- 通知正確病人的照顧者（10.15）----------------------------------------------


_LINKED = {(USER_ID, "U_GRANDPA")}
_STAY_WITH_GRANDPA = "請留在阿公身邊，並依紅卡立即尋求協助。"


async def _run_emergency(*people, members=(), links=_LINKED, sent=True, family=None):
    replier = FakeReplier()
    alert = FakeEmergencyAlertService(sent=sent, links=links)
    handler = _emergency_handler(
        _emergency_payload(),
        alert,
        replier=replier,
        urgency_classifier=_Identifier(*people),
        patient_context_service=family or _FamilyList(*members),
    )
    await handler.handle(_text_event())
    await _drain(handler)
    return alert, replier


async def test_self_emergency_notifies_own_family_with_own_words():
    """「我昏倒了」：病人就是發話者，原話照舊轉給家人，成功後告訴他。"""
    alert, replier = await _run_emergency(AffectedPerson(kind="self", event="昏倒"))

    assert alert.calls == [(USER_ID, "你提到有人跌倒", USER_TEXT)]
    assert alert.reporters == [USER_ID]
    assert len(replier.replies) == 1
    assert [text for _, text in replier.pushed_texts] == [
        t("text.emergency.family_notified", "zh-TW")
    ]


@pytest.mark.parametrize("event", ["跌倒", "昏迷"])
async def test_grandpa_emergency_notifies_grandpas_caregivers_not_the_reporters(event):
    """「我阿公跌倒／昏迷」：通知的是阿公的照顧者，不是孫子自己的家人。"""
    grandpa = AffectedPerson(kind="family", label="阿公", relationship="grandparent", event=event)
    alert, replier = await _run_emergency(grandpa, members=[_grandpa_member()])

    # 孫子的原話不是阿公說的，不轉；回報者另外記下。
    assert alert.calls == [("U_GRANDPA", "你提到有人跌倒", "")]
    assert alert.reporters == [USER_ID]
    # 發話者只收到稱謂提示；「我已經讓你的家人知道你現在需要有人陪」是對病人
    # 本人說的話，不送。
    assert [text for _, text in replier.pushed_texts] == [_STAY_WITH_GRANDPA]


async def test_two_grandparents_notify_nobody():
    alert, replier = await _run_emergency(
        _GRANDPA,
        members=[_grandpa_member("U_G1", "王大明"), _grandpa_member("U_G2", "李阿土")],
        links={(USER_ID, "U_G1"), (USER_ID, "U_G2")},
    )

    assert alert.calls == []
    assert len(replier.replies) == 1


@pytest.mark.parametrize(
    "person",
    [
        AffectedPerson(kind="third_party", label="路人", event="跌倒"),
        AffectedPerson(kind="third_party", label="朋友", event="想自殺"),
    ],
    ids=["passer-by-fell", "friend-suicidal"],
)
async def test_unlinked_third_parties_notify_nobody(person):
    """「路人跌倒了」「我朋友想自殺」：不通知發話者的家人，也不猜對方是誰。"""
    alert, replier = await _run_emergency(person, members=[_grandpa_member()])

    assert alert.calls == []
    assert [text for _, text in replier.pushed_texts] == [
        "請留在對方身邊，並依紅卡立即尋求協助。"
    ]


async def test_grandpa_and_self_in_one_message_notify_each_patients_family():
    urgent_self = AffectedPerson(kind="self", event="胸口痛到喘不過氣")
    alert, _ = await _run_emergency(_GRANDPA, urgent_self, members=[_grandpa_member()])

    assert alert.calls == [
        ("U_GRANDPA", "你提到有人跌倒", ""),
        (USER_ID, "你提到有人跌倒", USER_TEXT),
    ]


@pytest.mark.parametrize(
    "family",
    [_FamilyList(error=RuntimeError("mongo down")), _FamilyList(_grandpa_member(), delay=6)],
    ids=["lookup-fails", "lookup-times-out"],
)
async def test_family_lookup_problems_notify_nobody_but_keep_the_card(family, monkeypatch):
    import app.services.safety.emergency_alert_service as alert_module

    monkeypatch.setattr(alert_module, "RESOLVE_TIMEOUT_SECONDS", 0.01)
    alert, replier = await _run_emergency(_GRANDPA, family=family)

    assert alert.calls == []
    assert len(replier.replies) == 1


async def test_unlinked_grandpa_is_not_notified():
    """阿公在我的名單裡，但他的族譜裡沒有我：不自動通知。"""
    alert, _ = await _run_emergency(_GRANDPA, members=[_grandpa_member()], links=set())
    assert alert.calls == []


async def test_failed_notification_is_not_announced_and_keeps_the_card():
    alert, replier = await _run_emergency(
        AffectedPerson(kind="self", event="昏倒"), sent=False
    )

    assert len(alert.calls) == 1
    assert len(replier.replies) == 1
    assert replier.pushed_texts == []


async def test_unrecognized_people_are_treated_as_the_reporter():
    """人物辨識失敗（例如 LLM 中斷）：當成發話者本人，通知他自己的家人。"""
    alert, replier = await _run_emergency()
    assert alert.calls == [(USER_ID, "你提到有人跌倒", USER_TEXT)]
    assert len(replier.replies) == 1
    assert [text for _, text in replier.pushed_texts] == [
        t("text.emergency.family_notified", "zh-TW")
    ]
