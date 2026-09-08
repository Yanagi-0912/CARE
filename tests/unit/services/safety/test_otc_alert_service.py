"""非處方藥成分重複偵測的通知行為。

這裡驗的是「誰在什麼情況下收到什麼」，成分比對本身的判定留在
`test_ingredient_overlap.py`。
"""

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from app.services.safety.ingredient_overlap import IngredientWatchlist
from app.services.safety.otc_alert_service import OtcAlertService

WATCHLIST = IngredientWatchlist(["ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE"])


@dataclass
class _Entry:
    drug_class: str
    ingredients: tuple[str, ...]
    dosage_form: str = "膜衣錠"


@dataclass
class _Med:
    id: str
    name: str
    license_number: Optional[str] = None
    spc_indication_summary: Optional[str] = None


@dataclass
class _Reminder:
    medication_ids: list


class _Catalog:
    def __init__(self, by_licence: dict) -> None:
        self._by_licence = by_licence

    def entry_by_license_number(self, licence: str):
        return self._by_licence.get(licence)


class _MedRepo:
    def __init__(self, meds: dict) -> None:
        self._meds = meds

    async def find_by_ids(self, ids):
        return [self._meds[i] for i in ids if i in self._meds]

    async def find_active_by_ids(self, ids, date_str):
        return [self._meds[i] for i in ids if i in self._meds]


class _ReminderRepo:
    def __init__(self, reminders: list) -> None:
        self._reminders = reminders

    async def list_reminders_by_user(self, user_id):
        return self._reminders


class _Replier:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.flexes: list[tuple[str, Any]] = []

    async def push_text(self, user_id, text):
        self.texts.append((user_id, text))
        return True

    async def push_flex(self, user_id, flex_message):
        self.flexes.append((user_id, flex_message))
        return True


class _Auth:
    def __init__(self, recipients, raises: bool = False) -> None:
        self._recipients = recipients
        self._raises = raises
        self.kinds: list[str] = []

    async def notification_recipients(self, user_id, kind):
        self.kinds.append(kind)
        if self._raises:
            raise RuntimeError("boom")
        return list(self._recipients)


class _Profiles:
    async def get_user_profile(self, user_id):
        return {"name": f"名字-{user_id}", "settings": {"language": "zh-TW"}}


def _build(
    *,
    meds: dict,
    catalog: dict,
    reminders: list,
    recipients=("family-1",),
    auth_raises: bool = False,
    local_forms=frozenset(),
    profiles: Any = None,
):
    replier = _Replier()
    auth = _Auth(recipients, raises=auth_raises)
    service = OtcAlertService(
        catalog_service=_Catalog(catalog),
        medication_repository=_MedRepo(meds),
        reminder_repository=_ReminderRepo(reminders),
        replier=replier,
        watchlist=WATCHLIST,
        local_action_forms=local_forms,
        authorization_service=auth,
        user_profile_service=profiles if profiles is not None else _Profiles(),
    )
    return service, replier, auth


# --- 四種組合 -------------------------------------------------------------


@pytest.mark.asyncio
async def test_prescription_drug_notifies_nobody():
    """處方藥完全略過——連「新增了什麼藥」的通知都不發。

    它已經過醫師診斷與藥師調劑，再通知一次只是噪音，而通知量該與風險成正比。
    """
    service, replier, auth = _build(
        meds={"new": _Med(id="new", name="降血壓藥", license_number="L-RX")},
        catalog={"L-RX": _Entry("prescription", ("AMLODIPINE",))},
        reminders=[],
    )

    await service.check("patient", ["new"])

    assert replier.texts == []
    assert replier.flexes == []
    # 連收件人都不該去查——沒有要通知的事
    assert auth.kinds == []


@pytest.mark.asyncio
async def test_otc_without_overlap_notifies_family_only():
    """無重複時只通知家人：當事人剛完成加入動作，不需要再被打擾一次。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="止咳糖漿", license_number="L-A"),
            "old": _Med(id="old", name="胃藥", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("DEXTROMETHORPHAN",)),
            "L-B": _Entry("otc", ("MAGNESIUM OXIDE",)),
        },
        reminders=[_Reminder(medication_ids=["old"])],
    )

    await service.check("patient", ["new"])

    assert replier.texts == []
    assert [uid for uid, _ in replier.flexes] == ["family-1"]
    assert "新增了用藥提醒" in replier.flexes[0][1].alt_text


@pytest.mark.asyncio
async def test_otc_with_overlap_notifies_both_parties():
    service, replier, auth = _build(
        meds={
            "new": _Med(id="new", name="普拿疼", license_number="L-A"),
            "old": _Med(id="old", name="斯斯感冒膠囊", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("ACETAMINOPHEN",)),
            "L-B": _Entry("otc_guided", ("ACETAMINOPHEN", "CAFFEINE")),
        },
        reminders=[_Reminder(medication_ids=["old"])],
    )

    await service.check("patient", ["new"])

    assert [uid for uid, _ in replier.flexes] == ["family-1"]
    assert "用藥重複提醒" in replier.flexes[0][1].alt_text
    assert auth.kinds == ["otc_medication_added"]

    (recipient, text), = replier.texts
    assert recipient == "patient"
    assert "普拿疼" in text and "斯斯感冒膠囊" in text and "ACETAMINOPHEN" in text
    # SHALL 引導詢問藥師、SHALL NOT 指示停藥或給劑量
    assert "藥師" in text
    assert "停" not in text


@pytest.mark.asyncio
async def test_detection_failure_stays_silent():
    """偵測拋例外時不通知任何人，也不往外拋——對主流程 fail-open。"""

    class _Exploding:
        async def find_by_ids(self, ids):
            raise RuntimeError("catalog down")

    replier = _Replier()
    service = OtcAlertService(
        catalog_service=_Catalog({}),
        medication_repository=_Exploding(),
        reminder_repository=_ReminderRepo([]),
        replier=replier,
        watchlist=WATCHLIST,
        authorization_service=_Auth(("family-1",)),
    )

    await service.check("patient", ["new"])  # 不得拋出

    assert replier.texts == []
    assert replier.flexes == []


# --- 邊界 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_told_family_will_help_only_when_family_notified():
    """沒有合格收件人時，不能對當事人說「也讓家人幫你看一下」。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="普拿疼", license_number="L-A"),
            "old": _Med(id="old", name="感冒膠囊", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("ACETAMINOPHEN",)),
            "L-B": _Entry("otc", ("ACETAMINOPHEN",)),
        },
        reminders=[_Reminder(medication_ids=["old"])],
        recipients=(),
    )

    await service.check("patient", ["new"])

    assert replier.flexes == []
    (_, text), = replier.texts
    assert "家人" not in text
    assert "藥師" in text


@pytest.mark.asyncio
async def test_recipient_lookup_failure_still_warns_the_patient():
    """收件人查詢失敗對通報 fail-closed，但當事人那則仍然要送。

    重複是他自己吃的兩盒藥的事實，不因為族譜查不到而消失。
    """
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="普拿疼", license_number="L-A"),
            "old": _Med(id="old", name="感冒膠囊", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("ACETAMINOPHEN",)),
            "L-B": _Entry("otc", ("ACETAMINOPHEN",)),
        },
        reminders=[_Reminder(medication_ids=["old"])],
        auth_raises=True,
    )

    await service.check("patient", ["new"])

    assert replier.flexes == []
    assert len(replier.texts) == 1


@pytest.mark.asyncio
async def test_patient_never_receives_the_family_card():
    """當事人若同時是自己族譜裡的成員，也不該收到兩則。"""
    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="止咳糖漿", license_number="L-A")},
        catalog={"L-A": _Entry("otc", ("DEXTROMETHORPHAN",))},
        reminders=[],
        recipients=("patient", "family-1"),
    )

    await service.check("patient", ["new"])

    assert [uid for uid, _ in replier.flexes] == ["family-1"]


@pytest.mark.asyncio
async def test_two_new_drugs_in_one_scan_are_compared_against_each_other():
    """同一個藥袋裡的兩盒成藥重複，是這個功能最典型的情境。"""
    service, replier, _ = _build(
        meds={
            "a": _Med(id="a", name="感冒藥", license_number="L-A"),
            "b": _Med(id="b", name="止痛藥", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("ACETAMINOPHEN", "VITAMIN C")),
            "L-B": _Entry("otc", ("ACETAMINOPHEN",)),
        },
        reminders=[],
    )

    await service.check("patient", ["a", "b"])

    (_, text), = replier.texts
    assert "止痛藥" in text and "感冒藥" in text


@pytest.mark.asyncio
async def test_local_action_form_is_excluded_from_comparison_but_still_announced():
    """眼藥水不參與成分比對，但家人仍該知道家裡多了一盒非處方藥。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="益眼乙12眼藥水", license_number="L-EYE"),
            "old": _Med(id="old", name="小兒蜜咳樂糖漿", license_number="L-SYRUP"),
        },
        catalog={
            "L-EYE": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",), dosage_form="眼用液劑"),
            "L-SYRUP": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",), dosage_form="糖漿劑"),
        },
        reminders=[_Reminder(medication_ids=["old"])],
        local_forms=frozenset({"眼用液劑"}),
    )

    await service.check("patient", ["new"])

    assert replier.texts == []
    assert "新增了用藥提醒" in replier.flexes[0][1].alt_text


@pytest.mark.asyncio
async def test_unknown_drug_class_is_not_treated_as_otc():
    """藥證庫查無、或分級為空字串——不偵測也不通知。

    `classify_drug` 對認不得的類別回空字串而不猜，這裡承接同一個保守方向。
    """
    service, replier, auth = _build(
        meds={"new": _Med(id="new", name="來路不明的藥", license_number="L-?")},
        catalog={"L-?": _Entry("", ("ACETAMINOPHEN",))},
        reminders=[],
    )

    await service.check("patient", ["new"])

    assert replier.flexes == [] and replier.texts == []
    assert auth.kinds == []


@pytest.mark.asyncio
async def test_legacy_catalog_without_new_fields_does_not_raise():
    """執行期載入的藥證庫是尚未帶新欄位的舊版時，視為無成分資料而跳過。"""

    class _Legacy:
        pass  # 沒有 drug_class／ingredients／dosage_form

    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="某藥", license_number="L-OLD")},
        catalog={"L-OLD": _Legacy()},
        reminders=[],
    )

    await service.check("patient", ["new"])

    assert replier.flexes == [] and replier.texts == []


@pytest.mark.asyncio
async def test_family_card_uses_each_recipients_own_language():
    """語言與字級取收件人本人的設定，不是當事人的。"""

    class _MixedProfiles:
        async def get_user_profile(self, user_id):
            lang = {"family-1": "ja", "family-2": "en"}.get(user_id, "zh-TW")
            return {"name": "王大明", "settings": {"language": lang}}

    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="止咳糖漿", license_number="L-A")},
        catalog={"L-A": _Entry("otc", ("DEXTROMETHORPHAN",))},
        reminders=[],
        recipients=("family-1", "family-2"),
        profiles=_MixedProfiles(),
    )

    await service.check("patient", ["new"])

    alts = {uid: flex.alt_text for uid, flex in replier.flexes}
    assert "服薬リマインダー" in alts["family-1"]
    assert "added a medication reminder" in alts["family-2"]


@pytest.mark.asyncio
async def test_indication_reaches_the_card_but_never_the_alt_text():
    """用途進得了卡片內容，但 SHALL NOT 出現在 altText。

    altText 就是通知列與鎖定畫面上那一行，可能被非預期的人看到。
    """
    service, replier, _ = _build(
        meds={
            "new": _Med(
                id="new",
                name="普拿疼",
                license_number="L-A",
                spc_indication_summary="退燒、止痛",
            )
        },
        catalog={"L-A": _Entry("otc", ("ACETAMINOPHEN",))},
        reminders=[],
    )

    await service.check("patient", ["new"])

    _, flex = replier.flexes[0]
    assert "退燒" not in flex.alt_text
    assert "普拿疼" not in flex.alt_text
    assert "退燒、止痛" in str(flex.contents.to_dict())
