"""高風險通報的收件人由**通知政策**決定，不再是族譜全員。

對應 `specs/drug-safety-alert/spec.md` 的「通報收件人的通知政策」。這條通道是
唯一繞過 LIFF 授權邊界把健康資訊送出去的路徑——`MEMBER` 在 LIFF 裡連長輩的
年齡都看不到，卻會在通知列收到他的用藥風險，那不是設計，是這條通道當初沒有
授權可用。

授權服務用**真的** `FamilyAuthorizationService`，只把 repository 換成假的：
整包 mock 掉的話，通知政策與讀取權有沒有真的分開就驗不到了。
"""

from datetime import datetime, timezone

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.safety import DrugMention
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.medication.drug_catalog_service import (
    DrugCatalogEntry,
    DrugCatalogService,
)
from app.services.safety.safety_alert_service import SafetyAlertService

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
PATIENT = "U_PATIENT"
GUARDIAN = "U_GUARDIAN"
CAREGIVER = "U_CAREGIVER"
MEMBER = "U_MEMBER"
UNSET = "U_UNSET"

# high 的素材沿用既有測試那一組：藥名帶外文字符、通路為境外個人攜帶。
# 判定門檻一律留在 risk_rules，這裡不自己造一組「應該會是 high」的輸入。
UNAPPROVED = DrugMention(raw_name="合利他命強効錠 EX PLUS")
HIGH_RISK_TEXT = (
    "朋友從日本帶回來的合利他命強効錠 EX PLUS、パッケージは日本語です"
)


class FakeExtractor:
    def __init__(self, mentions):
        self._mentions = mentions

    async def extract(self, text: str):
        return self._mentions


class FakeAlertRepository:
    def __init__(self, granted: bool = True):
        self._granted = granted

    async def try_claim(self, **kwargs) -> bool:
        return self._granted


class FakeReplier:
    def __init__(self):
        self.flex_pushes = []
        self.text_pushes = []

    async def push_flex(self, user_id, flex):
        self.flex_pushes.append((user_id, flex))

    async def push_text(self, user_id, text):
        self.text_pushes.append((user_id, text))


class FakeProfileService:
    async def get_user_profile(self, user_id):
        return {"name": "王大明", "settings": {}}


class FakeTrees:
    def __init__(self, tree):
        self.tree = tree

    async def get_by_user_id(self, user_id):
        return self.tree if user_id == PATIENT else None


class ExplodingTrees:
    async def get_by_user_id(self, user_id):
        raise RuntimeError("Mongo 掛了")


class NoDelegations:
    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        return False


def family(members, state="enforced"):
    return FamilyTree(
        user_id=PATIENT,
        family_members=members,
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )


MIXED_FAMILY = [
    FamilyMember(user_id=GUARDIAN, family_role="GUARDIAN"),
    FamilyMember(user_id=CAREGIVER, family_role="CAREGIVER"),
    FamilyMember(user_id=MEMBER, family_role="MEMBER"),
    FamilyMember(user_id=UNSET),
]


def build(tree, *, trees_repo=None, granted=True):
    replier = FakeReplier()
    trees = trees_repo or FakeTrees(tree)
    authz = FamilyAuthorizationService(
        family_tree_repository=trees,
        delegation_repository=NoDelegations(),
        enforcement_enabled=True,
    )
    service = SafetyAlertService(
        extractor=FakeExtractor([UNAPPROVED]),
        catalog_service=DrugCatalogService(
            [DrugCatalogEntry(license_number="X", name_zh="普拿疼")], threshold=0.88
        ),
        alert_repository=FakeAlertRepository(granted),
        family_tree_repository=trees,
        replier=replier,
        user_profile_service=FakeProfileService(),
        dedupe_hours=24,
        authorization_service=authz,
    )
    return service, replier, authz


def recipients(replier):
    return {user_id for user_id, _ in replier.flex_pushes}


def patient_message(replier):
    texts = [text for user_id, text in replier.text_pushes if user_id == PATIENT]
    assert texts, "當事人那則一定要送"
    return texts[0]


# ── 收件人由通知政策產出 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recipients_are_guardian_and_caregiver_only():
    """第一版：`GUARDIAN` ＋ `CAREGIVER`。`MEMBER` 與未設定角色者不收。"""
    service, replier, _ = build(family(MIXED_FAMILY))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert recipients(replier) == {GUARDIAN, CAREGIVER}


@pytest.mark.asyncio
async def test_member_is_not_notified_despite_general_read():
    """`MEMBER` 有 GENERAL 讀取權，但 SHALL NOT 因此收到通報。

    這正是通知政策與讀取權分離的重點：他看得到長輩吃什麼藥，不代表該在
    通知列收到他的用藥風險。
    """
    service, replier, _ = build(family(MIXED_FAMILY))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert MEMBER not in recipients(replier)


@pytest.mark.asyncio
async def test_notification_does_not_grant_any_access():
    """收到通報 SHALL NOT 改變收件人的任何資料存取權。

    釘住這件事，是為了避免日後有人「順便」用通知路徑帶出額外資料——那會讓
    「通知獨立於讀取權」變成「用通知送出讀取權以外的資料」。
    """
    service, replier, authz = build(family(MIXED_FAMILY))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert CAREGIVER in recipients(replier)
    assert await authz.can(CAREGIVER, PATIENT, "PRIVATE", "READ") is False
    assert await authz.can(CAREGIVER, PATIENT, "SENSITIVE", "WRITE") is False


# ── 沒有合格收件人 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_qualified_recipient_still_notifies_the_patient():
    service, replier, _ = build(
        family([FamilyMember(user_id=MEMBER, family_role="MEMBER")])
    )

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert recipients(replier) == set()
    assert patient_message(replier)


@pytest.mark.asyncio
async def test_no_qualified_recipient_does_not_claim_family_was_told():
    """告訴長輩「我已經請家人一起看看」而其實沒有人收到，比不通知更糟。

    他會以為有人正在處理，於是不再自己找醫師。
    """
    service, replier, _ = build(
        family([FamilyMember(user_id=MEMBER, family_role="MEMBER")])
    )

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert "家人" not in patient_message(replier)


@pytest.mark.asyncio
async def test_with_recipients_the_patient_is_told_family_knows():
    """反過來：真的送出去了，那句話就該在。"""
    service, replier, _ = build(family(MIXED_FAMILY))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert "家人" in patient_message(replier)


@pytest.mark.asyncio
async def test_no_family_at_all_still_notifies_the_patient():
    """既有的降級行為不變：沒有族譜也要回覆當事人，且不得拋例外。"""
    service, replier, _ = build(family([]))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert recipients(replier) == set()
    assert "家人" not in patient_message(replier)


# ── 影子模式 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_mode_keeps_notifying_the_whole_family():
    """收斂收件人也是一種收緊，影子模式下不得生效。

    通報是使用者最不該「安靜地少收到」的一種訊息：導入前族譜全員都收得到，
    在沒有任何切換的情況下突然少收到，比看不到某個欄位嚴重得多。
    """
    service, replier, _ = build(family(MIXED_FAMILY, state="shadow"))

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert recipients(replier) == {GUARDIAN, CAREGIVER, MEMBER, UNSET}
    assert "家人" in patient_message(replier)


# ── 既有行為不得改變 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_content_still_carries_no_illness_or_original_text():
    """`通報內容與隱私` 一字未動：只含姓名、藥名與風險類型。

    收斂的是「送給誰」，不是「送什麼」。
    """
    service, replier, _ = build(family(MIXED_FAMILY))

    await service.check(PATIENT, HIGH_RISK_TEXT + "，我最近睡不著")

    for _, flex in replier.flex_pushes:
        serialized = str(flex)
        assert "睡不著" not in serialized
        assert "朋友從日本帶回來" not in serialized


@pytest.mark.asyncio
async def test_throttled_claim_sends_nothing_at_all():
    """`通報節流` 一字未動：沒取得通報權時連當事人都不打擾。"""
    service, replier, _ = build(family(MIXED_FAMILY), granted=False)

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert replier.flex_pushes == []
    assert replier.text_pushes == []


@pytest.mark.asyncio
async def test_recipient_lookup_failure_degrades_silently():
    """`失敗時的降級行為` 仍成立：查詢失敗記 log 後靜默結束。

    對主流程 fail-open、對通報 fail-closed——不通報任何人，但當事人那則仍要送，
    且整條路徑不得拋例外。
    """
    service, replier, _ = build(None, trees_repo=ExplodingTrees())

    await service.check(PATIENT, HIGH_RISK_TEXT)

    assert recipients(replier) == set()
    assert "家人" not in patient_message(replier)
