"""緊急家人通知的整合測試：找假元件測不出來的問題。

單元測試與 tests/unit/services/safety/test_emergency_pipeline.py 把授權、資料庫都換成
假的，它們只會照我們的假設回應。這裡換上真的元件：

- A1：跨輪代名詞。真的 handler／Agent／人物辨識流程，對話紀錄裡有前文（曾測出錯誤，已修正）。
- A3：真的 FamilyAuthorizationService，看替別人回報時實際的收件人。
- C1～C3：真的 MongoDB（CARE_e2e 資料庫的 it_emergency_claims／it_emergency_reports），
  驗證原子去重、TTL 索引、時區與共用節流表。

一律不呼叫 Gemini：LLM 的輸出依句子寫死（見 test_emergency_pipeline 的 SCRIPT）。

需要資料庫的測試只在設定 CARE_E2E_MONGODB_URI 時才跑，平常的 ./init.sh 不會碰任何
資料庫；只使用 CARE_e2e 底下這兩個 it_ 前綴的 collection，不動其他 collection。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.i18n.messages import t
from app.models.family_tree import FamilyMember, FamilyTree
from app.models.safety import EmergencyReportEntry
from app.repositories.emergency_report_repository import EmergencyReportRepository
from app.repositories.health_alert_claim_repository import HealthAlertClaimRepository
from app.services.family.family_authorization_service import FamilyAuthorizationService
from app.services.family.person_resolution import PersonResolution
from app.services.medical.symptom_classification.urgency import AffectedPerson
from app.services.safety.emergency_alert_service import (
    CLAIM_KEY,
    EmergencyFamilyAlertService,
    ResolvedAffected,
)
from tests.unit.services.safety import test_emergency_pipeline as pipeline_module
from tests.unit.services.safety.test_emergency_pipeline import (
    AUNT,
    GRANDPA,
    GRANDSON,
    MOM,
    Pipeline,
    _p,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
COUSIN = "U_COUSIN"


class LinePush:
    """只記錄推播對象；push 一律成功。"""

    def __init__(self):
        self.flex: list[str] = []
        self.texts: list[str] = []

    async def push_flex(self, user_id, flex):
        self.flex.append(user_id)
        return True

    async def push_text(self, user_id, text):
        self.texts.append(user_id)
        return True


class NoProfiles:
    async def get_user_profile(self, user_id):
        return {"name": user_id}


# --- A1：跨輪代名詞 ---------------------------------------------------------------
#
# 2026-09-25 這組測試確實測出錯誤：紅卡後的人物辨識只看得到這一則，「他現在叫不醒」
# 被當成發話者本人，通知了孫子自己的家人，卡片寫成「王小明 剛才說的話」。
# 修正：辨識時帶入發話者稍早的訊息；對不到是誰時回 someone_else，不當成本人。

PRONOUN_TEXT = "他現在叫不醒"
EARLIER = "我阿公剛剛跌倒"


def _earlier_block(prompt: str) -> str:
    """prompt 裡「前文：」那一段；prompt 本身的參考例句也含有「我阿公剛剛跌倒」，不能整份比對。"""
    return prompt.rsplit("前文：", 1)[1].split("這次的訊息：", 1)[0]


class PronounAwareLLM:
    """模擬模型看得到前文時的回答；判定與其他句子交給原本寫死的 SCRIPT。

    這是對模型行為的假設，不是實測（刻意不呼叫 Gemini）。測的是我們有沒有把前文
    交給模型，以及拿到 someone_else 時系統怎麼處置。
    """

    def __init__(self, fallback):
        self.fallback = fallback
        self.identify_prompts: list[str] = []

    async def __call__(self, prompt: str) -> dict:
        if "不要重新判斷是否緊急" not in prompt or PRONOUN_TEXT not in prompt:
            return await self.fallback(prompt)
        self.identify_prompts.append(prompt)
        if EARLIER in _earlier_block(prompt):
            return {"affected": [_p("grandparent", "阿公", "叫不醒")]}
        return {"affected": [_p("someone_else", "他", "叫不醒")]}


def _pronoun_pipeline(monkeypatch, *, history):
    monkeypatch.setitem(pipeline_module.SCRIPT, PRONOUN_TEXT, (True, []))
    monkeypatch.setitem(pipeline_module.SCRIPT_KEYS, PRONOUN_TEXT, PRONOUN_TEXT)
    pipeline = Pipeline()
    classifier = pipeline.handler._urgency_classifier
    llm = PronounAwareLLM(classifier._invoke)
    classifier._invoke = llm

    class History:
        async def load_history(self, **kwargs):
            return list(history)

        async def save_turn(self, **kwargs):
            pass

    pipeline.handler._history_service = History()
    return pipeline, llm


@pytest.mark.asyncio
async def test_a1_pronoun_in_the_next_turn_notifies_grandpas_caregiver(monkeypatch):
    """孫子先說「我阿公剛剛跌倒」，下一則「他現在叫不醒」：通知的是阿公的照顧者。"""
    pipeline, llm = _pronoun_pipeline(
        monkeypatch,
        history=[HumanMessage(content=EARLIER), AIMessage(content="請先確認阿公有沒有意識。")],
    )

    await pipeline.say(PRONOUN_TEXT)

    assert pipeline.red_card_first()
    assert EARLIER in _earlier_block(llm.identify_prompts[0]), "人物辨識沒拿到前文"
    assert pipeline.line.to("flex", MOM) == [], "把阿公的事件當成孫子本人，通知了孫子自己的家人"
    (card,) = pipeline.line.to("flex", AUNT)
    assert "王小明 回報的內容" in card and "剛才說" not in card


@pytest.mark.asyncio
async def test_a1_pronoun_without_earlier_context_notifies_nobody(monkeypatch):
    """前文對不到人時不猜：不通知任何家庭，也不當成發話者本人。"""
    pipeline, _ = _pronoun_pipeline(monkeypatch, history=[])

    await pipeline.say(PRONOUN_TEXT)

    assert pipeline.red_card_first()
    assert pipeline.line.to("flex", MOM) == []
    assert pipeline.line.to("flex", AUNT) == []
    assert pipeline.reporter_texts() == [
        t("text.emergency.result.other.not_notified", "zh-TW")
    ]


# --- A3：真的授權服務下，替別人回報的收件人 ---------------------------------------


class Trees:
    def __init__(self, state):
        self.tree = FamilyTree(
            user_id=GRANDPA,
            family_members=[
                FamilyMember(user_id=AUNT, display_name="王阿姨", family_role="GUARDIAN"),
                FamilyMember(user_id=COUSIN, display_name="王表哥"),  # 未指派角色＝MEMBER
                FamilyMember(user_id=GRANDSON, display_name="王小明"),  # 回報者，未指派角色
            ],
            rbac_migration_state=state,
            created_at=T0,
            updated_at=T0,
        )

    async def get_by_user_id(self, user_id):
        return self.tree if user_id == GRANDPA else None


class NoDelegations:
    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        return False


def _grandpa_resolved():
    member = FamilyMember(user_id=GRANDPA, display_name="王大明", relationship_type="grandparent")
    return (
        ResolvedAffected(
            AffectedPerson(kind="family", label="阿公", relationship="grandparent", event="跌倒"),
            PersonResolution(kind="member", member=member, display_label="王大明"),
        ),
    )


async def _report_with_real_authorization(state):
    line = LinePush()
    authorization = FamilyAuthorizationService(
        family_tree_repository=Trees(state),
        delegation_repository=NoDelegations(),
        enforcement_enabled=True,
    )
    service = EmergencyFamilyAlertService(
        replier=line, authorization_service=authorization, user_profile_service=NoProfiles()
    )
    outcomes = await service.report(GRANDSON, _grandpa_resolved(), "你提到有人跌倒")
    return outcomes, line


@pytest.mark.asyncio
async def test_a3_member_role_reporter_can_report_for_grandpa():
    """孫子在阿公族譜裡只是未指派角色（MEMBER），仍可回報（v1：任何角色皆可）。"""
    outcomes, _ = await _report_with_real_authorization("enforced")
    assert outcomes == {GRANDPA: "sent"}


@pytest.mark.asyncio
async def test_a3_enforced_family_only_notifies_guardians_and_caregivers():
    _, line = await _report_with_real_authorization("enforced")
    assert line.flex == [AUNT]


@pytest.mark.asyncio
async def test_a3_shadow_family_notifies_everyone_in_grandpas_tree():
    """現況記錄：還沒啟用角色權限的家庭（影子模式），收件人是阿公族譜的所有成員。

    emergency_detected 不在 STRICT_NOTIFICATION_KINDS，影子模式下不照政策篩選，
    所以沒有照顧角色的表哥也會收到。這條測試記錄的是現在的行為，是否要改屬產品決定。
    """
    _, line = await _report_with_real_authorization("shadow")
    assert sorted(line.flex) == sorted([AUNT, COUSIN])


# --- C1～C3：真的 MongoDB（CARE_e2e）-----------------------------------------------

E2E_URI = os.getenv("CARE_E2E_MONGODB_URI", "")
E2E_DB = "CARE_e2e"
CLAIMS = "it_emergency_claims"
REPORTS = "it_emergency_reports"

needs_mongo = pytest.mark.skipif(
    not E2E_URI, reason="未設定 CARE_E2E_MONGODB_URI，略過需要真實 MongoDB 的整合測試"
)


@pytest.fixture
async def e2e_collections():
    """只碰 CARE_e2e 的兩個 it_ collection：開始前 drop 重建索引，結束後清空資料。"""
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(E2E_URI, tz_aware=False)
    db = client[E2E_DB]
    claims, reports = db[CLAIMS], db[REPORTS]
    await claims.drop()
    await reports.drop()
    await HealthAlertClaimRepository.ensure_indexes(collection=claims)
    await EmergencyReportRepository.ensure_indexes(collection=reports)
    yield claims, reports
    await claims.delete_many({})
    await reports.delete_many({})
    client.close()


class ClaimsOn:
    """把正式的 HealthAlertClaimRepository 綁到測試 collection。"""

    def __init__(self, collection):
        self.collection = collection

    async def try_claim(self, user_id, alert_key, ttl_minutes):
        return await HealthAlertClaimRepository.try_claim(
            user_id, alert_key, ttl_minutes, collection=self.collection
        )

    async def release(self, user_id, alert_key):
        await HealthAlertClaimRepository.release(user_id, alert_key, collection=self.collection)


class ReportsOn:
    def __init__(self, collection):
        self.collection = collection

    async def append_many(self, entries):
        await EmergencyReportRepository.append_many(entries, collection=self.collection)

    async def count_cross_person_sent(self, **kwargs):
        return await EmergencyReportRepository.count_cross_person_sent(
            collection=self.collection, **kwargs
        )


class LinkedAuthorization:
    async def resolve_role(self, operator_id, target_owner_id, now=None):
        return "MEMBER"

    async def notification_recipients(self, owner_id, kind):
        return [AUNT]


@needs_mongo
@pytest.mark.asyncio
async def test_c1_concurrent_reports_for_the_same_patient_notify_once(e2e_collections):
    """兩個孫子同時回報阿公：唯一索引是去重的唯一保證，假資料庫測不出競爭條件。"""
    claims, reports = e2e_collections
    line = LinePush()
    service = EmergencyFamilyAlertService(
        replier=line,
        authorization_service=LinkedAuthorization(),
        user_profile_service=NoProfiles(),
        report_repository=ReportsOn(reports),
        claim_repository=ClaimsOn(claims),
    )

    results = await asyncio.gather(
        *(service.report(f"U_REPORTER_{i}", _grandpa_resolved(), "你提到有人跌倒") for i in range(8))
    )

    outcomes = sorted(r[GRANDPA] for r in results)
    assert outcomes.count("sent") == 1
    assert outcomes.count("duplicate") == 7
    assert line.flex == [AUNT]
    assert await claims.count_documents({"user_id": GRANDPA, "alert_key": CLAIM_KEY}) == 1
    assert await reports.count_documents({}) == 8


@needs_mongo
@pytest.mark.asyncio
async def test_c2_indexes_are_created_as_production_expects(e2e_collections):
    claims, reports = e2e_collections

    claim_indexes = {i["name"]: i async for i in claims.list_indexes()}
    unique = next(i for i in claim_indexes.values() if list(i["key"]) == ["user_id", "alert_key"])
    assert unique.get("unique") is True
    ttl = next(i for i in claim_indexes.values() if list(i["key"]) == ["expires_at"])
    assert ttl.get("expireAfterSeconds") == 0

    report_keys = [list(i["key"].items()) async for i in reports.list_indexes()]
    assert [("reporter_id", 1), ("reported_at", -1)] in report_keys
    assert [("patient_id", 1), ("reported_at", -1)] in report_keys
    report_ttl = [i async for i in reports.list_indexes() if list(i["key"]) == ["expires_at"]]
    assert report_ttl and report_ttl[0].get("expireAfterSeconds") == 0


@needs_mongo
@pytest.mark.asyncio
async def test_c2_times_round_trip_as_utc_and_expire_after_60_days(e2e_collections):
    """存進去的時間要以 UTC 的同一個時刻比較；TTL 看的就是這個值。"""
    _, reports = e2e_collections
    reported_at = datetime(2026, 9, 25, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    entry = EmergencyReportEntry(
        report_id="r1",
        reporter_id=GRANDSON,
        patient_id=GRANDPA,
        person_kind="family",
        cross_person=True,
        outcome="sent",
        reported_at=reported_at,
        expires_at=reported_at + timedelta(days=60),
    )
    await EmergencyReportRepository.append_many([entry], collection=reports)

    stored = await reports.find_one({"report_id": "r1"})
    as_utc = reported_at.astimezone(timezone.utc).replace(tzinfo=None)
    assert stored["reported_at"] == as_utc
    assert stored["expires_at"] - stored["reported_at"] == timedelta(days=60)


@needs_mongo
@pytest.mark.asyncio
async def test_c2_rate_limit_counts_compare_instants_across_time_zones(e2e_collections):
    """計數用帶時區的 since 查詢，資料庫裡是 UTC；兩者要比對同一個時刻。"""
    _, reports = e2e_collections
    now = datetime.now(timezone.utc)
    entries = [
        EmergencyReportEntry(
            report_id=f"r{i}", reporter_id=GRANDSON, patient_id=GRANDPA, person_kind="family",
            cross_person=True, outcome="sent", reported_at=at, expires_at=at + timedelta(days=60),
        )
        for i, at in enumerate((now - timedelta(minutes=30), now - timedelta(hours=2)))
    ]
    await EmergencyReportRepository.append_many(entries, collection=reports)

    since_taipei = (now - timedelta(hours=1)).astimezone(timezone(timedelta(hours=8)))
    assert await EmergencyReportRepository.count_cross_person_sent(
        patient_id=GRANDPA, since=since_taipei, collection=reports
    ) == 1


@needs_mongo
@pytest.mark.asyncio
async def test_c2_claim_expiry_is_ten_minutes_after_the_claim(e2e_collections):
    claims, _ = e2e_collections
    assert await HealthAlertClaimRepository.try_claim(GRANDPA, CLAIM_KEY, 10, collection=claims)
    stored = await claims.find_one({"user_id": GRANDPA, "alert_key": CLAIM_KEY})
    assert stored["expires_at"] - stored["claimed_at"] == timedelta(minutes=10)


@needs_mongo
@pytest.mark.asyncio
async def test_c3_emergency_and_health_alert_claims_do_not_block_each_other(e2e_collections):
    """緊急去重借用健康提醒的節流表：同一位病人的血壓提醒與緊急通報各自獨立。"""
    claims, _ = e2e_collections

    assert await HealthAlertClaimRepository.try_claim(GRANDPA, "bp_high", 30, collection=claims)
    assert await HealthAlertClaimRepository.try_claim(GRANDPA, CLAIM_KEY, 10, collection=claims)
    assert not await HealthAlertClaimRepository.try_claim(GRANDPA, CLAIM_KEY, 10, collection=claims)

    await HealthAlertClaimRepository.release(GRANDPA, CLAIM_KEY, collection=claims)

    assert await claims.count_documents({"user_id": GRANDPA, "alert_key": "bp_high"}) == 1
    assert not await HealthAlertClaimRepository.try_claim(GRANDPA, "bp_high", 30, collection=claims)
    assert await HealthAlertClaimRepository.try_claim(GRANDPA, CLAIM_KEY, 10, collection=claims)
