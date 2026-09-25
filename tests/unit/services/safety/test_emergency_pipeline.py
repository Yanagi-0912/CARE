"""緊急流程的整合驗收（tasks 10.19）。

從 LINE 文字訊息進來，一路走真實元件：LineMessageHandler → Agent（guardrail 節點、
緊急短路）→ UrgencyClassifier（判定與紅卡後的人物辨識）→ PatientContextService
（發話者的家庭名單）→ EmergencyFamilyAlertService（連結驗證、限流、去重、稽核、
家人通報卡）→ 給發話者的結果訊息。

只替換外部邊界：LLM 的回覆（依句子寫死）、MongoDB（族譜、profile、稽核、節流）
與 LINE API（回覆與推播）。斷言的是使用者與家屬實際看到的東西，以及稽核紀錄。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.i18n.messages import t
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.agent.agent import Agent
from app.services.family.patient_context_service import PatientContextService
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.medical.symptom_classification.urgency import UrgencyClassifier
from app.services.rag.answer_prompts import CONTEXT_BEGIN, CONTEXT_END
from app.services.safety.emergency_alert_service import (
    DEDUPE_MINUTES,
    EmergencyFamilyAlertService,
)

GRANDSON = "U_GRANDSON"  # 發話者：王小明
GRANDPA = "U_GRANDPA"  # 王大明，發話者的阿公
GRANDPA_2 = "U_GRANDPA_2"  # 李阿土，另一位阿公（歧義情境才加入名單）
AUNT = "U_AUNT"  # 阿公的照顧者
MOM = "U_MOM"  # 發話者自己的照顧者
T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

RED_CARD_ALT = "請立即就醫"


# --- LLM：依句子寫死判定與人物 --------------------------------------------------


def _p(relation, label="", event="", urgent=True):
    return {"relation": relation, "label": label, "event": event, "urgent": urgent}


# 句子 → (是否緊急, 紅卡後辨識出的人物)。判定欄位與正式 prompt 相同；是不是緊急
# 由 LLM 決定，這裡驗的是「判定之後系統怎麼處置」。
SCRIPT: dict[str, tuple[bool, list[dict]]] = {
    "我阿公跌倒了，叫不醒": (True, [_p("grandparent", "阿公", "跌倒叫不醒")]),
    "路邊有人昏倒了": (True, [_p("not_family", "路人", "昏倒")]),
    "我朋友傳訊息說他不想活了": (True, [_p("not_family", "朋友", "想結束生命")]),
    "我阿公昨天跌倒，已經去急診處理了，現在想問回診看哪科": (False, []),
    "我阿公跌倒叫不醒，我自己也胸口痛到喘不過氣": (
        True,
        [_p("grandparent", "阿公", "跌倒叫不醒"), _p("self", "", "胸痛喘不過氣")],
    ),
    "我昏倒了": (True, [_p("self", "", "昏倒")]),
}
SCRIPT_KEYS = {text: text for text in SCRIPT}


class ScriptedUrgencyLLM:
    def __init__(self):
        self.decisions: list[str] = []
        self.identifications: list[str] = []

    async def __call__(self, prompt: str) -> dict:
        # 只看資料邊界裡的使用者原文：prompt 本身的參考例句也含有這些句子。
        data = prompt.rsplit(CONTEXT_BEGIN, 1)[1].split(CONTEXT_END, 1)[0].strip()
        text = SCRIPT_KEYS[data]
        emergency, people = SCRIPT[text]
        if "不要重新判斷是否緊急" in prompt:
            self.identifications.append(text)
            return {"affected": people}
        self.decisions.append(text)
        return {
            "happening_now": emergency,
            "needs_immediate_care": emergency,
            "display": "你提到有人失去意識" if emergency else "",
        }


class AgentLLM:
    def __init__(self):
        self.invocations = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.invocations += 1
        return AIMessage(content="回診可以先掛骨科或家醫科。")


class Guardrail:
    async def allow_rag_tool(self, text):
        return False


@pytest.fixture(autouse=True)
def _isolated_rag_tool():
    """非緊急時 agent 可能強制轉 RAG；固定成假服務，避免連到真實向量庫。"""
    import app.tools.rag_tools as rag_tools

    class _Rag:
        async def answer(self, query: str) -> str:
            return "知識庫回覆"

    previous = rag_tools._rag_answer_service
    rag_tools.configure_rag_tool(_Rag())
    yield
    rag_tools.configure_rag_tool(previous)


# --- MongoDB 與 LINE 的替身 -----------------------------------------------------


def _tree(owner, *members):
    return FamilyTree(user_id=owner, family_members=list(members), created_at=T0, updated_at=T0)


def _member(user_id, name, relationship):
    return FamilyMember(user_id=user_id, display_name=name, relationship_type=relationship)


class Trees:
    """族譜：發話者的名單決定稱謂；病人的名單決定回報者有沒有連結。"""

    def __init__(self, *, two_grandpas=False, grandpa_links_back=True):
        grandson_members = [_member(GRANDPA, "王大明", "grandparent"), _member(MOM, "王媽媽", "parent")]
        if two_grandpas:
            grandson_members.append(_member(GRANDPA_2, "李阿土", "grandparent"))
        grandpa_members = [_member(AUNT, "王阿姨", "child")]
        if grandpa_links_back:
            grandpa_members.append(_member(GRANDSON, "王小明", "grandchild"))
        self.trees = {
            GRANDSON: _tree(GRANDSON, *grandson_members),
            GRANDPA: _tree(GRANDPA, *grandpa_members),
            GRANDPA_2: _tree(GRANDPA_2, _member(GRANDSON, "王小明", "grandchild")),
        }

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)


class Authorization:
    """連結看病人族譜；收件人照 emergency_detected 政策（這裡直接給名單）。"""

    def __init__(self, trees, recipients):
        self.trees = trees
        self.recipients = recipients
        self.sensitive_reads = []

    async def resolve_role(self, operator_id, target_owner_id, now=None):
        tree = self.trees.trees.get(target_owner_id)
        linked = tree is not None and any(m.user_id == operator_id for m in tree.family_members)
        return "MEMBER" if linked else None

    async def notification_recipients(self, owner_id, kind):
        assert kind == "emergency_detected"
        return list(self.recipients.get(owner_id, ()))

    async def authorize(self, *args, **kwargs):
        # 回報緊急事件不得走健康資料讀取授權。
        self.sensitive_reads.append(args)
        raise AssertionError("緊急回報不得要求 SENSITIVE READ")


class Profiles:
    def __init__(self, *, disabled=()):
        self.values = {
            GRANDSON: {"name": "王小明", "settings": {"language": "zh-TW"}},
            GRANDPA: {"name": "王大明", "age": 82},
            GRANDPA_2: {"name": "李阿土"},
            AUNT: {"name": "王阿姨", "settings": {"notify_family": AUNT not in disabled}},
            MOM: {"name": "王媽媽", "settings": {"notify_family": MOM not in disabled}},
        }

    async def get_user_profile(self, user_id):
        return self.values.get(user_id)


class Reports:
    def __init__(self):
        self.entries = []

    async def append_many(self, entries):
        self.entries.extend(entries)

    async def count_cross_person_sent(self, *, since, reporter_id=None, patient_id=None):
        return sum(
            1
            for e in self.entries
            if e.cross_person and e.outcome == "sent" and e.reported_at >= since
            and (reporter_id is None or e.reporter_id == reporter_id)
            and (patient_id is None or e.patient_id == patient_id)
        )


class Claims:
    def __init__(self, clock):
        self.clock = clock
        self.held = {}

    async def try_claim(self, user_id, alert_key, ttl_minutes):
        expires = self.held.get((user_id, alert_key))
        if expires is not None and expires > self.clock():
            return False
        self.held[(user_id, alert_key)] = self.clock() + timedelta(minutes=ttl_minutes)
        return True

    async def release(self, user_id, alert_key):
        self.held.pop((user_id, alert_key), None)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Line:
    """LINE API：記錄先後順序；push_ok=False 模擬推播全部失敗。"""

    def __init__(self, *, push_ok=True):
        self.push_ok = push_ok
        self.log: list[tuple[str, str, str]] = []

    async def reply(self, **kwargs):
        self.log.append(("reply", kwargs.get("user_id"), kwargs.get("message_text")))
        return True

    async def push_flex(self, user_id, flex):
        if not self.push_ok:
            return False
        self.log.append(("flex", user_id, json.dumps(flex.contents.to_dict(), ensure_ascii=False)))
        return True

    async def push_text(self, user_id, text):
        if self.push_ok or user_id == GRANDSON:
            self.log.append(("text", user_id, text))
            return True
        return False

    def to(self, kind, user_id):
        return [body for k, uid, body in self.log if k == kind and uid == user_id]


class History:
    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        pass


class Pipeline:
    def __init__(self, *, two_grandpas=False, grandpa_links_back=True, recipients=None,
                 disabled=(), push_ok=True):
        self.clock = Clock()
        self.llm = ScriptedUrgencyLLM()
        self.agent_llm = AgentLLM()
        self.line = Line(push_ok=push_ok)
        self.reports = Reports()
        trees = Trees(two_grandpas=two_grandpas, grandpa_links_back=grandpa_links_back)
        profiles = Profiles(disabled=disabled)
        self.authorization = Authorization(
            trees, recipients if recipients is not None else {GRANDPA: [AUNT], GRANDSON: [MOM]}
        )
        classifier = UrgencyClassifier(invoke=self.llm)
        alert_service = EmergencyFamilyAlertService(
            replier=self.line,
            authorization_service=self.authorization,
            user_profile_service=profiles,
            report_repository=self.reports,
            claim_repository=Claims(self.clock),
            clock=self.clock,
        )
        self.handler = LineMessageHandler(
            agent=Agent(llm=self.agent_llm, guardrail_service=Guardrail(), urgency_classifier=classifier),
            history_service=History(),
            user_profile_service=profiles,
            replier=self.line,
            emergency_family_alert_service=alert_service,
            urgency_classifier=classifier,
            patient_context_service=PatientContextService(
                family_tree_repository=trees,
                authorization_service=self.authorization,
                user_profile_service=profiles,
            ),
        )

    async def say(self, text: str) -> None:
        event = MessageEvent(
            timestamp=int(self.clock().timestamp() * 1000),
            mode="active",
            webhookEventId="01HZEMERGENCYPIPELINE0000",
            deliveryContext=DeliveryContext(isRedelivery=False),
            replyToken="rt",
            source=UserSource(type="user", userId=GRANDSON),
            message=TextMessageContent(id="M1", text=text, quoteToken="qt"),
        )
        await self.handler.handle(event)
        while self.handler._safety_alert_tasks:
            await asyncio.gather(*list(self.handler._safety_alert_tasks))

    # 讀取用的小工具
    def first(self):
        return self.line.log[0]

    def red_card_first(self) -> bool:
        kind, user_id, body = self.first()
        return kind == "reply" and user_id == GRANDSON and json.loads(body)["altText"] == RED_CARD_ALT

    def reporter_texts(self):
        return self.line.to("text", GRANDSON)

    def audit(self):
        return [(e.person_kind, e.patient_id, e.cross_person, e.outcome) for e in self.reports.entries]


def _say(key, **kwargs):
    return t(key, "zh-TW").format(**kwargs)


OTHER_NOT_NOTIFIED = _say("text.emergency.result.other.not_notified")


# --- 情境 ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grandpa_fell_notifies_grandpas_caregiver_as_a_report_by_the_grandson():
    """「我阿公跌倒了，叫不醒」：紅卡先到；阿公的照顧者收到標明孫子回報的卡。"""
    pipeline = Pipeline()

    await pipeline.say("我阿公跌倒了，叫不醒")

    assert pipeline.red_card_first()
    (card,) = pipeline.line.to("flex", AUNT)
    assert "王小明 剛才在 CARE 回報 王大明 的狀況" in card
    assert "王小明 回報的內容" in card
    assert "我阿公跌倒了，叫不醒" in card
    assert "剛才說" not in card
    # 發話者自己的家人沒有被驚動，發話者也不會收到對病人本人說的話。
    assert pipeline.line.to("flex", MOM) == []
    assert pipeline.reporter_texts() == [_say("text.emergency.result.member.sent", name="阿公")]
    assert pipeline.audit() == [("family", GRANDPA, True, "sent")]
    assert pipeline.authorization.sensitive_reads == []


@pytest.mark.asyncio
async def test_two_grandpas_are_ambiguous_and_nobody_is_notified():
    pipeline = Pipeline(two_grandpas=True)

    await pipeline.say("我阿公跌倒了，叫不醒")

    assert pipeline.red_card_first()
    assert [k for k, _, _ in pipeline.line.log if k == "flex"] == []
    assert pipeline.reporter_texts() == [OTHER_NOT_NOTIFIED]
    assert "阿公" not in pipeline.reporter_texts()[0]
    assert pipeline.audit() == [("family", None, False, "not_notified")]
    assert pipeline.reports.entries[0].resolution_kind == "ambiguous"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "label"),
    [("路邊有人昏倒了", "路人"), ("我朋友傳訊息說他不想活了", "朋友")],
    ids=["passer-by-collapsed", "friend-self-harm"],
)
async def test_unlinked_third_party_gets_the_card_but_no_family_is_notified(text, label):
    pipeline = Pipeline()

    await pipeline.say(text)

    assert pipeline.red_card_first()
    assert [k for k, _, _ in pipeline.line.log if k == "flex"] == []
    assert pipeline.reporter_texts() == [OTHER_NOT_NOTIFIED]
    assert pipeline.audit() == [("third_party", None, False, "not_notified")]
    assert pipeline.reports.entries[0].label == label


@pytest.mark.asyncio
async def test_a_past_fall_that_was_already_treated_is_not_an_emergency():
    """「我阿公昨天跌倒，已經去急診處理了」：不出紅卡、不辨識人物、不通知、不留稽核。"""
    pipeline = Pipeline()

    await pipeline.say("我阿公昨天跌倒，已經去急診處理了，現在想問回診看哪科")

    ((kind, _, body),) = pipeline.line.log
    assert kind == "reply"
    assert RED_CARD_ALT not in body
    assert pipeline.agent_llm.invocations >= 1
    assert pipeline.llm.identifications == []
    assert pipeline.reports.entries == []


@pytest.mark.asyncio
async def test_grandpa_and_self_in_one_message_notify_both_families_separately():
    pipeline = Pipeline()

    await pipeline.say("我阿公跌倒叫不醒，我自己也胸口痛到喘不過氣")

    assert pipeline.red_card_first()
    (grandpa_card,) = pipeline.line.to("flex", AUNT)
    (own_card,) = pipeline.line.to("flex", MOM)
    assert "王小明 回報的內容" in grandpa_card
    assert "王小明 剛才說的話" in own_card
    assert "回報" not in own_card
    assert pipeline.reporter_texts() == [
        "\n".join(
            [
                _say("text.emergency.result.member.sent", name="阿公"),
                _say("text.emergency.result.self.sent"),
                _say("text.emergency.self_also_urgent"),
            ]
        )
    ]
    assert pipeline.audit() == [
        ("family", GRANDPA, True, "sent"),
        ("self", GRANDSON, False, "sent"),
    ]
    assert len({e.report_id for e in pipeline.reports.entries}) == 1


@pytest.mark.asyncio
async def test_self_emergency_notifies_own_family_with_own_words():
    pipeline = Pipeline()

    await pipeline.say("我昏倒了")

    assert pipeline.red_card_first()
    (card,) = pipeline.line.to("flex", MOM)
    assert "王小明 剛才說的話" in card and "我昏倒了" in card
    assert pipeline.reporter_texts() == [_say("text.emergency.result.self.sent")]


# --- 各種通知結果 --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "outcome"),
    [
        ({"recipients": {GRANDPA: [], GRANDSON: [MOM]}}, "no_recipient"),
        ({"disabled": (AUNT,)}, "disabled"),
        ({"push_ok": False}, "failed"),
    ],
    ids=["no-recipient", "recipient-disabled", "push-failed"],
)
async def test_undelivered_grandpa_notification_is_never_claimed_as_sent(setup, outcome):
    pipeline = Pipeline(**setup)

    await pipeline.say("我阿公跌倒了，叫不醒")

    assert pipeline.red_card_first()
    assert pipeline.line.to("flex", AUNT) == []
    (text,) = pipeline.reporter_texts()
    assert text == _say(f"text.emergency.result.member.{outcome}", name="阿公")
    assert "已通知" not in text
    assert pipeline.audit() == [("family", GRANDPA, True, outcome)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "outcome"),
    [
        ({"recipients": {GRANDPA: [AUNT], GRANDSON: []}}, "no_recipient"),
        ({"disabled": (MOM,)}, "disabled"),
        ({"push_ok": False}, "failed"),
    ],
    ids=["no-recipient", "recipient-disabled", "push-failed"],
)
async def test_undelivered_self_notification_is_never_claimed_as_sent(setup, outcome):
    pipeline = Pipeline(**setup)

    await pipeline.say("我昏倒了")

    assert pipeline.red_card_first()
    (text,) = pipeline.reporter_texts()
    assert text == _say(f"text.emergency.result.self.{outcome}")
    assert "已通知" not in text


@pytest.mark.asyncio
async def test_grandpa_who_does_not_list_the_grandson_is_not_notified():
    """阿公在孫子的名單裡，但阿公的族譜裡沒有孫子：連結未確認，不自動通知。"""
    pipeline = Pipeline(grandpa_links_back=False)

    await pipeline.say("我阿公跌倒了，叫不醒")

    assert pipeline.line.to("flex", AUNT) == []
    assert pipeline.reporter_texts() == [_say("text.emergency.result.member.not_notified", name="阿公")]
    assert pipeline.audit() == [("family", GRANDPA, True, "not_linked")]


@pytest.mark.asyncio
async def test_repeating_the_same_emergency_notifies_the_caregiver_once():
    pipeline = Pipeline()

    await pipeline.say("我阿公跌倒了，叫不醒")
    pipeline.clock.now += timedelta(seconds=30)
    await pipeline.say("我阿公跌倒了，叫不醒")

    assert len(pipeline.line.to("flex", AUNT)) == 1
    assert [e.outcome for e in pipeline.reports.entries] == ["sent", "duplicate"]
    assert pipeline.reporter_texts()[-1] == _say(
        "text.emergency.result.member.duplicate", name="阿公"
    )
    # 紅卡兩次都照送。
    assert [k for k, _, _ in pipeline.line.log].count("reply") == 2


@pytest.mark.asyncio
async def test_too_many_reports_about_grandpa_are_rate_limited_but_the_card_still_goes_out():
    pipeline = Pipeline()

    for _ in range(4):
        await pipeline.say("我阿公跌倒了，叫不醒")
        pipeline.clock.now += timedelta(minutes=DEDUPE_MINUTES + 1)

    assert len(pipeline.line.to("flex", AUNT)) == 3
    assert [e.outcome for e in pipeline.reports.entries] == ["sent", "sent", "sent", "rate_limited"]
    assert pipeline.reporter_texts()[-1] == _say(
        "text.emergency.result.member.rate_limited", name="阿公"
    )
    assert [k for k, _, _ in pipeline.line.log].count("reply") == 4


@pytest.mark.asyncio
async def test_red_card_never_waits_for_the_followup():
    """每一個緊急情境，紅卡都是第一則，且早於任何人物辨識之後的動作。"""
    for text, (emergency, _) in SCRIPT.items():
        if not emergency:
            continue
        pipeline = Pipeline()
        await pipeline.say(text)
        assert pipeline.red_card_first(), text
