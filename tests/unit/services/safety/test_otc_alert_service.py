"""非處方藥成分重複偵測的通知行為。

這裡驗的是「誰在什麼情況下收到什麼」，成分比對本身的判定留在
`test_ingredient_overlap.py`。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pytest

from app.services.safety.ingredient_overlap import IngredientClass, IngredientWatchlist
from app.services.medication.drug_catalog_service import DrugCatalogEntry
from app.services.medication.tcm_catalog_service import TcmCatalogEntry, TcmCatalogService
from app.services.safety.atc_interaction import ClassPairTable
from app.services.safety.otc_alert_service import OtcAlertService
from app.services.safety.tcm_interaction import TcmInteractionTable
from app.models.medication import TAIPEI_TZ

WATCHLIST = IngredientWatchlist(["ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE"])
# 抗膽鹼疊加清單。CHLORPHENIRAMINE 同時在兩份清單上是刻意的——真實資料就是
# 這樣（它既是最常重複的成分，也是最常見的第一代抗組織胺），優先序因此必須
# 被測試釘住。
ANTICHOLINERGICS = IngredientClass(
    ["CHLORPHENIRAMINE MALEATE", "DIPHENHYDRAMINE HCL", "DIMENHYDRINATE"]
)


def _Entry(
    drug_class: str,
    ingredients: tuple[str, ...],
    dosage_form: str = "膜衣錠",
    atc_codes: tuple[str, ...] = (),
) -> DrugCatalogEntry:
    """建真的 `DrugCatalogEntry`，不是形狀相符的自製 stub。

    這裡曾經是一個只帶四個欄位的 dataclass，而那正是「局部作用劑型不參與
    比對」從上線到 2026-09-18 一次都沒生效的原因：真的 `DrugCatalogEntry`
    沒有 `dosage_form` 欄位，服務端 `getattr(entry, "dosage_form", "")` 因此
    永遠取到空字串，而 stub 自己帶了這個欄位，於是下面那些測試全部是綠的。

    用真結構建，測試才問得出「服務讀的欄位真的存在嗎」——形狀相符的假物件
    只能回答「如果它存在，邏輯對不對」。
    """
    return DrugCatalogEntry(
        license_number="L-TEST",
        name_zh="測試藥",
        drug_class=drug_class,
        ingredients=tuple(ingredients),
        dosage_form=dosage_form,
        atc_codes=tuple(atc_codes),
    )


@dataclass
class _Med:
    id: str
    name: str
    license_number: Optional[str] = None
    spc_indication_summary: Optional[str] = None


class _Catalog:
    def __init__(self, by_licence: dict) -> None:
        self._by_licence = by_licence

    def entry_by_license_number(self, licence: str):
        return self._by_licence.get(licence)


class _MedRepo:
    """藥品庫的假物件。

    `existing_ids` 是「該使用者目前啟用且在效期內」的藥——服務端現在直接查
    這個，不再從提醒規則反推。這裡刻意沒有任何提醒的概念：一顆藥有沒有掛
    提醒（PRN 就沒有）與它該不該進比對池無關。
    """

    def __init__(self, meds: dict, existing_ids: list) -> None:
        self._meds = meds
        self._existing_ids = list(existing_ids)
        self.active_queries: list[tuple[str, str]] = []

    async def find_by_ids(self, ids):
        return [self._meds[i] for i in ids if i in self._meds]

    async def list_active_by_user(self, user_id, date_str):
        self.active_queries.append((user_id, date_str))
        return [self._meds[i] for i in self._existing_ids if i in self._meds]


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
    existing: list,
    recipients=("family-1",),
    auth_raises: bool = False,
    local_forms=frozenset(),
    profiles: Any = None,
    anticholinergics: Any = None,
    tcm_catalog: Any = None,
    tcm_interactions: Any = None,
    class_pairs: Any = None,
    tcm_watch_herbs: Any = None,
):
    replier = _Replier()
    auth = _Auth(recipients, raises=auth_raises)
    service = OtcAlertService(
        catalog_service=_Catalog(catalog),
        medication_repository=_MedRepo(meds, existing),
        replier=replier,
        watchlist=WATCHLIST,
        anticholinergics=(
            ANTICHOLINERGICS if anticholinergics is None else anticholinergics
        ),
        class_pairs=ClassPairTable(()) if class_pairs is None else class_pairs,
        tcm_watch_herbs=(
            IngredientWatchlist(()) if tcm_watch_herbs is None else tcm_watch_herbs
        ),
        tcm_catalog_service=tcm_catalog,
        tcm_interactions=(
            TcmInteractionTable(()) if tcm_interactions is None else tcm_interactions
        ),
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
        existing=[],
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
        existing=["old"],
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
        existing=["old"],
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
        replier=replier,
        watchlist=WATCHLIST,
        authorization_service=_Auth(("family-1",)),
    )

    await service.check("patient", ["new"])  # 不得拋出

    assert replier.texts == []
    assert replier.flexes == []


# --- 現有用藥池 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_prn_drug_without_any_reminder_is_still_compared():
    """需要時（PRN）的藥沒掛在任何提醒上，仍然要進比對池。

    成藥止痛藥最典型的用法就是「痛的時候才吃」，而 PRN 一律不掛提醒
    （`PrescriptionScanService._link_reminders`）。之前現有用藥是從提醒規則
    反推的，那盒止痛藥因此在之後每一次比對裡都是隱形的——布洛芬配抗凝血劑
    這一組正好漏在這裡。
    """
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="感冒熱飲", license_number="L-NEW"),
            "prn": _Med(id="prn", name="普拿疼", license_number="L-PRN"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE")),
            "L-PRN": _Entry("otc", ("ACETAMINOPHEN",)),
        },
        # 只有藥品本身「啟用且在效期內」，沒有任何提醒的關聯
        existing=["prn"],
    )

    await service.check("patient", ["new"])

    (recipient, text), = replier.texts
    assert recipient == "patient"
    assert "感冒熱飲" in text and "普拿疼" in text and "ACETAMINOPHEN" in text
    assert "用藥重複提醒" in replier.flexes[0][1].alt_text


@pytest.mark.asyncio
async def test_existing_pool_is_the_patients_active_medications_today():
    """現有用藥直接查「該使用者、今天仍有效」，不繞提醒規則。

    查的對象是服藥的人（家屬代掃時是長輩），日期是台北時間的今天——
    療程已結束的藥由 repository 的日期窗濾掉，這裡只確認問的是對的問題。
    """
    service, _, _ = _build(
        meds={"new": _Med(id="new", name="止咳糖漿", license_number="L-A")},
        catalog={"L-A": _Entry("otc", ("DEXTROMETHORPHAN",))},
        existing=[],
    )

    await service.check("elder-7", ["new"])

    repo = service._medication_repository
    assert repo.active_queries == [("elder-7", datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d"))]


@pytest.mark.asyncio
async def test_added_drugs_are_not_double_counted_as_existing():
    """剛加入的藥也會被「當日有效」查出來，但它不能同時算作現有用藥——
    否則每一盒新成藥都會和自己重複。"""
    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="普拿疼", license_number="L-A")},
        catalog={"L-A": _Entry("otc", ("ACETAMINOPHEN",))},
        existing=["new"],
    )

    await service.check("patient", ["new"])

    assert replier.texts == [], "不得和自己比出重複"
    assert "新增了用藥提醒" in replier.flexes[0][1].alt_text


@pytest.mark.asyncio
async def test_prescription_drug_in_the_same_submission_is_compared_against():
    """同一張藥袋裡的處方藥要當現有用藥，和同袋的成藥／中藥比。

    之前「現有用藥」排掉本次全部 id、觸發側又只留成藥與中藥，於是同袋的
    處方藥兩邊都不在——只差一次提交，布洛芬配可邁丁就從此互不相見。
    """
    service, replier, _ = _build(
        meds={
            "rx": _Med(id="rx", name="可邁丁錠", license_number="L-RX"),
            "otc": _Med(id="otc", name="布洛芬錠", license_number="L-OTC"),
        },
        catalog={
            "L-RX": _Entry("prescription", ("WARFARIN SODIUM",), atc_codes=("B01AA03",)),
            "L-OTC": _Entry("otc_guided", ("IBUPROFEN",), atc_codes=("M01AE01",)),
        },
        existing=[],
        class_pairs=CLASS_PAIRS,
    )

    await service.check("patient", ["rx", "otc"])

    (recipient, text), = replier.texts
    assert recipient == "patient"
    assert "布洛芬錠" in text and "可邁丁錠" in text
    assert "出血" in replier.flexes[0][1].alt_text


@pytest.mark.asyncio
async def test_same_submission_prescription_alone_still_notifies_nobody():
    """兩顆都是處方藥時仍然整條不啟動——上面那條只是把處方藥放進比對池，
    不是把它變成觸發側。"""
    service, replier, auth = _build(
        meds={
            "rx1": _Med(id="rx1", name="可邁丁錠", license_number="L-RX1"),
            "rx2": _Med(id="rx2", name="希樂葆", license_number="L-RX2"),
        },
        catalog={
            "L-RX1": _Entry("prescription", ("WARFARIN SODIUM",), atc_codes=("B01AA03",)),
            "L-RX2": _Entry("prescription", ("CELECOXIB",), atc_codes=("M01AH01",)),
        },
        existing=[],
        class_pairs=CLASS_PAIRS,
    )

    await service.check("patient", ["rx1", "rx2"])

    assert replier.texts == [] and replier.flexes == [] and auth.kinds == []


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
        existing=["old"],
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
        existing=["old"],
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
        existing=[],
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
        existing=[],
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
        existing=["old"],
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
        existing=[],
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
        existing=[],
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
        existing=[],
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
        existing=[],
    )

    await service.check("patient", ["new"])

    _, flex = replier.flexes[0]
    assert "退燒" not in flex.alt_text
    assert "普拿疼" not in flex.alt_text
    assert "退燒、止痛" in str(flex.contents.to_dict())


# --- 抗膽鹼疊加 -----------------------------------------------------------
#
# 補上成分重複抓不到的那一半：兩個藥含**不同**成分，但作用會加在一起。
# 實測藥證庫，含第一代抗組織胺的非處方藥 3,099 種，其中 2,112 種含
# CHLORPHENIRAMINE——同成分那三分之二由成分重複涵蓋，這條規則補的是
# 另外 987 種的交叉組合（感冒藥 + 暈車藥）。


@pytest.mark.asyncio
async def test_stacking_notifies_family_and_patient():
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="綜合感冒膠囊", license_number="L-NEW"),
            "old": _Med(id="old", name="暈車藥", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",)),
            "L-OLD": _Entry("otc", ("DIMENHYDRINATE",)),
        },
        existing=["old"],
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"]
    assert [u for u, _ in replier.texts] == ["patient-1"]
    ((_, text),) = replier.texts
    assert "綜合感冒膠囊" in text and "暈車藥" in text
    # 措辭紅線：SHALL NOT 指示停藥、SHALL NOT 給劑量建議。
    assert "停" not in text
    assert "劑量" not in text
    # 講他自己感覺得到的後果，那是當事人當下唯一能採取的安全動作。
    assert "小心" in text


@pytest.mark.asyncio
async def test_overlap_takes_precedence_over_stacking():
    """兩者同時成立時只發成分重複那則。

    使用者該做的事一模一樣（把兩盒藥拿去問藥師），發兩則只會稀釋，而
    「每一則都值得看」是這條通道全部價值的來源。
    """
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="綜合感冒膠囊", license_number="L-NEW"),
            "old": _Med(id="old", name="鼻炎膠囊", license_number="L-OLD"),
        },
        catalog={
            # 兩邊都有 CHLORPHENIRAMINE（重複），新藥另含 DIPHENHYDRAMINE（疊加）
            "L-NEW": _Entry("otc", ("CHLORPHENIRAMINE MALEATE", "DIPHENHYDRAMINE HCL")),
            "L-OLD": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",)),
        },
        existing=["old"],
    )

    await service.check("patient-1", ["new"])

    assert len(replier.texts) == 1
    assert "相同成分" in replier.texts[0][1]


@pytest.mark.asyncio
async def test_stacking_within_one_submission():
    """同一個藥袋裡買了感冒藥又買了暈車藥——這條規則最典型的情境。"""
    service, replier, _ = _build(
        meds={
            "a": _Med(id="a", name="綜合感冒膠囊", license_number="L-A"),
            "b": _Med(id="b", name="暈車藥", license_number="L-B"),
        },
        catalog={
            "L-A": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",)),
            "L-B": _Entry("otc", ("DIMENHYDRINATE",)),
        },
        existing=[],
    )

    await service.check("patient-1", ["a", "b"])

    assert len(replier.texts) == 1


@pytest.mark.asyncio
async def test_local_action_form_is_excluded_from_stacking():
    """點眼劑的抗組織胺全身吸收量可忽略，這種疊加沒有臨床意義。

    排除只作用在比對上——家人仍會收到「新增了非處方藥」的通知。
    """
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="抗過敏眼藥水", license_number="L-NEW"),
            "old": _Med(id="old", name="暈車藥", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",), dosage_form="點眼液劑"),
            "L-OLD": _Entry("otc", ("DIMENHYDRINATE",)),
        },
        existing=["old"],
        local_forms=frozenset({"點眼液劑"}),
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"]
    assert replier.texts == [], "當事人不該收到疊加提醒"


@pytest.mark.asyncio
async def test_empty_anticholinergic_list_disables_stacking_only():
    """清單載入失敗時退化成「不偵測疊加」，成分重複與新增通知不受影響。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="綜合感冒膠囊", license_number="L-NEW"),
            "old": _Med(id="old", name="暈車藥", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",)),
            "L-OLD": _Entry("otc", ("DIMENHYDRINATE",)),
        },
        existing=["old"],
        anticholinergics=IngredientClass(()),
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"], "新增通知仍要發"
    assert replier.texts == []


@pytest.mark.asyncio
async def test_stacking_with_no_family_uses_solo_wording():
    """沒有合格收件人時不能說「也讓家人幫你看一下」。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="綜合感冒膠囊", license_number="L-NEW"),
            "old": _Med(id="old", name="暈車藥", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("CHLORPHENIRAMINE MALEATE",)),
            "L-OLD": _Entry("otc", ("DIMENHYDRINATE",)),
        },
        existing=["old"],
        recipients=(),
    )

    await service.check("patient-1", ["new"])

    assert replier.flexes == []
    ((_, text),) = replier.texts
    assert "家人" not in text


# --- 中西藥交互作用 -------------------------------------------------------
#
# 補上健保雲端藥歷與現有規則都看不到的那一格：藥局買的成藥（雲端藥歷沒有）
# 配上中藥（成分是中文名，字串比對永遠對不上西藥學名）。橋接靠的是衛福部
# 維護的配對表，不是我們推測的對應。

GE_GEN = TcmCatalogEntry(code="P025", name_zh="葛根湯", herbs=("葛根", "麻黃", "桂枝"))
TCM_CATALOG = TcmCatalogService([GE_GEN])
TCM_PAIRS = TcmInteractionTable(
    [
        {"tcm": "葛根湯", "western": "ASPIRIN", "western_kind": "ingredient", "summary": "機制未明"},
        {"tcm": "甘草", "western": "利尿劑", "western_kind": "class", "summary": ""},
    ]
)


@pytest.mark.asyncio
async def test_new_tcm_against_existing_western_notifies():
    """自費看中醫拿了葛根湯，家裡還有醫師開的阿斯匹靈。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="順天堂葛根湯濃縮顆粒"),
            "old": _Med(id="old", name="阿斯匹靈腸溶錠", license_number="L-OLD"),
        },
        catalog={"L-OLD": _Entry("prescription", ("ASPIRIN",))},
        existing=["old"],
        tcm_catalog=TCM_CATALOG,
        tcm_interactions=TCM_PAIRS,
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"]
    ((_, text),) = replier.texts
    assert "葛根湯" in text and "阿斯匹靈腸溶錠" in text
    # 先破除「中藥溫和、可以配著吃」，否則後面導向藥師會被當成小題大作。
    assert "溫和" in text
    # 措辭紅線不變。
    assert "停" not in text and "劑量" not in text


@pytest.mark.asyncio
async def test_new_otc_against_existing_tcm_notifies():
    """反方向：在吃中藥期間自己去買了成藥。只查一個方向會漏掉一半。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="阿斯匹靈錠", license_number="L-NEW"),
            "old": _Med(id="old", name="葛根湯"),
        },
        catalog={"L-NEW": _Entry("otc", ("ASPIRIN",))},
        existing=["old"],
        tcm_catalog=TCM_CATALOG,
        tcm_interactions=TCM_PAIRS,
    )

    await service.check("patient-1", ["new"])

    ((_, text),) = replier.texts
    assert "阿斯匹靈錠" in text and "葛根湯" in text


@pytest.mark.asyncio
async def test_tcm_alone_triggers_no_interaction_message():
    """只加了中藥、沒有對得上的西藥——家人仍收到「新增了藥」，當事人不打擾。"""
    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="葛根湯")},
        catalog={},
        existing=[],
        tcm_catalog=TCM_CATALOG,
        tcm_interactions=TCM_PAIRS,
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"]
    assert replier.texts == []


@pytest.mark.asyncio
async def test_overlap_takes_precedence_over_tcm():
    """三條規則的優先序：成分重複 → 疊加 → 中西藥。一次只發一則。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="阿斯匹靈錠", license_number="L-NEW"),
            "old1": _Med(id="old1", name="葛根湯"),
            "old2": _Med(id="old2", name="普拿疼", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc", ("ASPIRIN", "ACETAMINOPHEN")),
            "L-OLD": _Entry("otc", ("ACETAMINOPHEN",)),
        },
        existing=["old1", "old2"],
        tcm_catalog=TCM_CATALOG,
        tcm_interactions=TCM_PAIRS,
    )

    await service.check("patient-1", ["new"])

    assert len(replier.texts) == 1
    assert "相同成分" in replier.texts[0][1]


@pytest.mark.asyncio
async def test_empty_tcm_table_disables_only_that_rule():
    """配對表載入失敗時退化成「不偵測中西藥」，其餘不受影響。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="葛根湯"),
            "old": _Med(id="old", name="阿斯匹靈腸溶錠", license_number="L-OLD"),
        },
        catalog={"L-OLD": _Entry("prescription", ("ASPIRIN",))},
        existing=["old"],
        tcm_catalog=TCM_CATALOG,
        tcm_interactions=TcmInteractionTable(()),
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"], "新增通知仍要發"
    assert replier.texts == []


@pytest.mark.asyncio
async def test_without_tcm_catalog_nothing_is_identified_as_tcm():
    """未註冊中藥庫時行為與過去完全一致——中藥不被辨識，這條通道不啟動。"""
    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="葛根湯")},
        catalog={},
        existing=[],
        tcm_catalog=None,
    )

    await service.check("patient-1", ["new"])

    assert replier.flexes == []
    assert replier.texts == []


# --- 出血風險與中藥材重複 -------------------------------------------------

CLASS_PAIRS = ClassPairTable.load_from_path()
TCM_HERBS = IngredientWatchlist(["甘草", "炙甘草", "麻黃"])


@pytest.mark.asyncio
async def test_otc_nsaid_against_prescribed_anticoagulant():
    """成藥止痛藥 × 處方抗凝血劑。三條成分層級的規則全部落空，但這是最重要的
    成藥 × 處方藥組合之一，而健保雲端藥歷也看不到成藥那一側。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="布洛芬錠", license_number="L-NEW"),
            "old": _Med(id="old", name="可邁丁錠", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc_guided", ("IBUPROFEN",), atc_codes=("M01AE01",)),
            "L-OLD": _Entry("prescription", ("WARFARIN SODIUM",), atc_codes=("B01AA03",)),
        },
        existing=["old"],
        class_pairs=CLASS_PAIRS,
    )

    await service.check("patient-1", ["new"])

    ((_, text),) = replier.texts
    assert "布洛芬錠" in text and "可邁丁錠" in text
    # 要讓人現在就注意得到的徵兆。
    assert "黑" in text
    # 抗凝血劑自行停用會中風，比出血更嚴重——這則 SHALL 明講不要自行停藥。
    assert "不要自己停掉" in text


@pytest.mark.asyncio
async def test_bleeding_outranks_the_other_rules():
    """優先序即嚴重度：出血是四條規則裡唯一可能致命的。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="止痛感冒錠", license_number="L-NEW"),
            "old": _Med(id="old", name="可邁丁錠", license_number="L-OLD"),
        },
        catalog={
            # 兩邊都有 ACETAMINOPHEN（成分重複也會成立），但出血優先。
            "L-NEW": _Entry("otc", ("IBUPROFEN", "ACETAMINOPHEN"), atc_codes=("M01AE01",)),
            "L-OLD": _Entry(
                "prescription", ("WARFARIN SODIUM", "ACETAMINOPHEN"), atc_codes=("B01AA03",)
            ),
        },
        existing=["old"],
        class_pairs=CLASS_PAIRS,
    )

    await service.check("patient-1", ["new"])

    assert len(replier.texts) == 1
    assert "出血" in replier.texts[0][1]


@pytest.mark.asyncio
async def test_two_tcm_formulas_sharing_a_watched_herb():
    """兩帖方含同一味藥材，與兩盒成藥含同一個成分是同一件事，用的也是同一個
    判定函式（find_overlap），只是換一份白名單。"""
    ge_gen = TcmCatalogEntry(code="P025", name_zh="葛根湯", herbs=("葛根", "麻黃", "炙甘草"))
    shao_yao = TcmCatalogEntry(code="P030", name_zh="芍藥甘草湯", herbs=("白芍", "炙甘草"))
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="葛根湯"),
            "old": _Med(id="old", name="芍藥甘草湯"),
        },
        catalog={},
        existing=["old"],
        tcm_catalog=TcmCatalogService([ge_gen, shao_yao]),
        tcm_watch_herbs=TCM_HERBS,
    )

    await service.check("patient-1", ["new"])

    ((_, text),) = replier.texts
    assert "炙甘草" in text


@pytest.mark.asyncio
async def test_common_herbs_outside_the_watchlist_are_not_reported():
    """茯苓、當歸、白芍在基準方裡各出現 50~63 次，兩帖共用是常態。
    全比對會讓警報被它們淹沒——與西藥白名單排除維生素同一個理由。"""
    a = TcmCatalogEntry(code="P1", name_zh="甲方", herbs=("當歸", "茯苓"))
    b = TcmCatalogEntry(code="P2", name_zh="乙方", herbs=("當歸", "白芍"))
    service, replier, _ = _build(
        meds={"new": _Med(id="new", name="甲方"), "old": _Med(id="old", name="乙方")},
        catalog={},
        existing=["old"],
        tcm_catalog=TcmCatalogService([a, b]),
        tcm_watch_herbs=TCM_HERBS,
    )

    await service.check("patient-1", ["new"])

    assert replier.texts == []


@pytest.mark.asyncio
async def test_missing_atc_codes_disable_only_the_bleeding_rule():
    """atc_codes 覆蓋率 58.4%，查無 ATC 是常態，退化方向是少偵測。"""
    service, replier, _ = _build(
        meds={
            "new": _Med(id="new", name="布洛芬錠", license_number="L-NEW"),
            "old": _Med(id="old", name="可邁丁錠", license_number="L-OLD"),
        },
        catalog={
            "L-NEW": _Entry("otc_guided", ("IBUPROFEN",)),
            "L-OLD": _Entry("prescription", ("WARFARIN SODIUM",)),
        },
        existing=["old"],
        class_pairs=CLASS_PAIRS,
    )

    await service.check("patient-1", ["new"])

    assert [u for u, _ in replier.flexes] == ["family-1"], "新增通知仍要發"
    assert replier.texts == []
