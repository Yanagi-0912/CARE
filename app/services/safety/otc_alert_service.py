"""非處方藥加入用藥提醒後的成分重複偵測與通知。

判定門檻留在 `ingredient_overlap`（純函式），副作用留在這裡——比照
`risk_rules` 與 `SafetyAlertService` 既有的分工。

## 為什麼一次提交只發一則

一個藥袋可能同時加入三個非處方藥。逐藥發送會讓家人一口氣收到三則推播，而
第三則的閱讀率遠低於第一則。因此本服務以「一次提交」為單位：最多一則給家人、
最多一則給當事人。

## 新藥彼此之間也要比

同一次掃描裡的兩盒成藥都含乙醯胺酚，是這個功能最典型的情境（「感冒藥」和
「止痛藥」被當成兩種不同的東西一起買回家）。因此比對的另一邊不只是既有用藥，
也包含這次提交裡先前處理過的藥；同一次提交裡的處方藥則直接算現有用藥。

## 對主流程 fail-open

呼叫端是掃描提交流程，使用者正在等頁面回應，而且藥已經寫進資料庫了。這條
路徑上任何失敗都只能記 log 後靜默結束——一則「偵測失敗」對使用者沒有任何
可行動的意義，卻會讓他以為加入提醒本身出了問題。

## log 不得帶藥名、成分、姓名、機構

用藥組合本身即為病史的強烈線索（「乙醯胺酚 + 攝護腺用藥」洩漏的資訊遠超過
任一單項）。因此這裡一律只記例外型別名稱與數量，**不使用 `logger.exception`**
——traceback 會把例外訊息一併寫出，而例外訊息常帶著查詢參數。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol, Sequence

from app.i18n import t
from app.models.medication import TAIPEI_TZ
from app.services.line_messaging.flex.otc_flex import build_otc_family_flex
from app.services.safety.atc_interaction import ClassPairTable, find_class_pair
from app.services.safety.tcm_interaction import (
    TcmInteractionTable,
    find_tcm_interaction,
)
from app.services.safety.ingredient_overlap import (
    IngredientClass,
    IngredientWatchlist,
    find_class_stacking,
    find_overlap,
    is_local_action,
    should_check,
)
from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

_FALLBACK_PATIENT_NAME = "家人"

NOTIFICATION_KIND = "otc_medication_added"


class _Replier(Protocol):
    async def push_text(self, user_id: str, text: str) -> bool: ...
    async def push_flex(self, user_id: str, flex_message: Any) -> bool: ...


@dataclass(frozen=True)
class _DrugView:
    """比對與組訊息需要的最小切面。"""

    medication_id: str
    name: str
    ingredients: tuple[str, ...]
    dosage_form: str
    drug_class: str
    indication: Optional[str] = None
    # 這筆藥若被認出是基準方，這裡放方名與其組成藥材（方名在最前面）。
    # 中藥沒有藥證字號，走的是 `TcmCatalogService` 的方名比對，與西藥那條
    # `license_number` → `drug_catalog` 的路徑完全獨立。
    tcm_keys: tuple[str, ...] = ()
    # 該張藥證的 ATC 藥理治療分類碼。類別層級的配對（出血風險）靠它，
    # 成分層級的兩條規則不讀它。
    atc_codes: tuple[str, ...] = ()

    @property
    def is_otc(self) -> bool:
        return should_check(self.drug_class)

    @property
    def is_tcm(self) -> bool:
        return bool(self.tcm_keys)

    @property
    def tcm_name(self) -> str:
        return self.tcm_keys[0] if self.tcm_keys else ""


class OtcAlertService:
    """非處方藥加入提醒後的偵測與通報。

    收件人走 `FamilyAuthorizationService.notification_recipients(user_id,
    "otc_medication_added")`——通知政策與資料存取授權是兩套獨立的表，收到通知
    SHALL NOT 改變收件人的任何資料存取權。
    """

    def __init__(
        self,
        catalog_service: Any,
        medication_repository: Any,
        replier: _Replier,
        watchlist: IngredientWatchlist,
        anticholinergics: IngredientClass = IngredientClass(()),
        class_pairs: ClassPairTable = ClassPairTable(()),
        tcm_watch_herbs: IngredientWatchlist = IngredientWatchlist(()),
        tcm_catalog_service: Any = None,
        tcm_interactions: TcmInteractionTable = TcmInteractionTable(()),
        local_action_forms: frozenset = frozenset(),
        authorization_service: Any = None,
        user_profile_service: Any = None,
    ) -> None:
        self._catalog_service = catalog_service
        self._medication_repository = medication_repository
        self._replier = replier
        self._watchlist = watchlist
        self._anticholinergics = anticholinergics
        self._class_pairs = class_pairs
        self._tcm_watch_herbs = tcm_watch_herbs
        self._tcm_catalog_service = tcm_catalog_service
        self._tcm_interactions = tcm_interactions
        self._local_action_forms = local_action_forms
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service

    async def check(self, patient_user_id: str, added_medication_ids: Sequence[str]) -> None:
        """對外的唯一入口。任何失敗都吞在這裡，主流程不受影響。"""
        if not patient_user_id or not added_medication_ids:
            return
        try:
            await self._check(patient_user_id, list(added_medication_ids))
        except Exception as exc:  # noqa: BLE001 - 對主流程 fail-open
            logger.warning("非處方藥成分偵測失敗，本次不通知：%s", type(exc).__name__)

    async def _check(self, patient_user_id: str, added_medication_ids: list[str]) -> None:
        added = await self._views(added_medication_ids)
        # 中藥與非處方藥同列觸發條件：兩者都是「沒有任何專業判斷把關」的用藥
        # ——長輩自己去藥局買成藥，或自費看中醫、自行到中藥房抓藥，都不會有
        # 醫師看到他的完整用藥清單。**處方藥那條線不動**：它仍然只在比對池裡
        # 當「現有用藥」，不作為觸發側。
        new_otc = [v for v in added if v.is_otc or v.is_tcm]
        if not new_otc:
            # 全是處方藥、非成品藥或分級不明——這條通道整個不啟動，連「新增了
            # 什麼藥」的通知都不發。處方藥已經過醫師診斷與藥師調劑，再通知一次
            # 只是噪音，而通知量該與風險成正比。
            return

        existing = await self._existing_views(patient_user_id, set(added_medication_ids))
        # 同一次提交裡**不是**觸發側的藥（處方藥）也要當現有用藥：醫院中醫部的
        # 藥袋常同時有西藥處方與中藥方，兩者只差一次提交就從此互不相見——
        # 「現有用藥」排掉本次 id 是為了不讓新藥和自己比，不是要排掉同袋的處方藥。
        # 觸發側的藥彼此之間怎麼比由各條規則的 pool 累加負責，這裡不重複放。
        existing.extend(v for v in added if v not in new_otc)
        # 出血排最前面：它是四條規則裡唯一可能致命的，而其餘三條的後果
        # （過量、頭暈跌倒、併用影響）雖然嚴重但層級不同。優先序即嚴重度。
        bleeding = self._first_class_pair(new_otc, existing)
        overlap = None if bleeding else self._first_overlap(new_otc, existing)
        # 成分重複優先：兩者同時成立時只發重複那則。使用者該做的事一模一樣
        # （把兩盒藥拿去問藥師），發兩則只會稀釋，而「每一則都值得看」是這條
        # 通道全部價值的來源。沒有重複時才找疊加。
        stacking = (
            None if (bleeding or overlap) else self._first_stacking(new_otc, existing)
        )
        # 中西藥交互作用排在最後：前兩條講的是「同一種作用吃了兩份」，是可以
        # 用一句話說完的事；這一條牽涉兩套不同的用藥體系，訊息本來就比較難懂，
        # 前面兩條有結果時不必再多說一則。
        tcm = (
            None
            if (bleeding or overlap or stacking)
            else self._first_tcm_interaction(new_otc, existing)
        )
        finding = bleeding or overlap or stacking or tcm

        # 家人先發、當事人後發：當事人那則的措辭取決於家人是否真的收到了
        # （沒有合格收件人時不能說「也讓家人幫你看一下」）。
        notified_family = await self._notify_family(
            patient_user_id, new_otc[0], overlap, stacking, tcm, bleeding
        )
        if finding is not None:
            kind = (
                "bleeding"
                if bleeding
                else ("overlap" if overlap else ("stacking" if stacking else "tcm"))
            )
            await self._notify_patient(patient_user_id, finding, notified_family, kind)

    # ---- 偵測 --------------------------------------------------------------

    def _comparable(self, view: _DrugView) -> bool:
        """這筆藥要不要進比對。

        要有可比對的內容——西藥成分，**或**中藥的組成藥材。中藥沒有西藥成分，
        只看 `ingredients` 會讓它整個進不了比對池（兩帖方共用甘草因此永遠抓
        不到）。

        局部作用劑型（點眼、含漱等）排除：全身吸收量可忽略，這種重複沒有臨床
        意義。**排除只作用在比對上，不影響「新增了非處方藥」的通知**——家人
        仍然該知道家裡多了一盒不用處方就能買到的藥。
        """
        return bool(view.ingredients or view.tcm_keys) and not is_local_action(
            view.dosage_form, self._local_action_forms
        )

    def _first_overlap(
        self, new_otc: list[_DrugView], existing: list[_DrugView]
    ) -> Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]]:
        """回報第一組重複，不是全部。

        一則訊息裡塞進三組重複，長輩讀不完也分不清該問哪一個；而只要有任何
        一組成立，該做的事都一樣——把兩盒藥拿去問藥師。多找到的組合會在下次
        加入時（或家人打開清單時）自然浮現。

        比對的另一邊包含這次提交裡先前的藥：同一個藥袋裡的兩盒成藥重複，正是
        最典型的情境。
        """
        pool = [v for v in existing if self._comparable(v)]
        for candidate in new_otc:
            if not self._comparable(candidate):
                pool.append(candidate)
                continue
            for other in pool:
                finding = find_overlap(candidate.ingredients, other.ingredients, self._watchlist)
                if finding:
                    return candidate, other, finding.ingredients
                # 中藥之間用同一個判定函式，只是換一份白名單：兩帖方含同一味
                # 藥材，與兩盒成藥含同一個成分是同一件事。實測 200 個基準方裡
                # 130 方含甘草或炙甘草，重複的機會極高。
                herbs = find_overlap(
                    candidate.tcm_keys[1:], other.tcm_keys[1:], self._tcm_watch_herbs
                )
                if herbs:
                    return candidate, other, herbs.ingredients
            pool.append(candidate)
        return None

    def _first_stacking(
        self, new_otc: list[_DrugView], existing: list[_DrugView]
    ) -> Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]]:
        """回報第一組抗膽鹼疊加，形狀與 `_first_overlap` 相同。

        判準的差別只在成分關係：那邊要「同一個成分出現在兩邊」，這邊要「兩個
        **不同**成分各自都在抗膽鹼清單上」。相同成分的情形由 `find_class_stacking`
        自己排除——那已經是成分重複的範圍，見該函式的說明。

        比對池與「只報第一組」的規則完全沿用 `_first_overlap`，包括把這次提交
        裡先前處理過的藥也放進池子：同一個藥袋裡買了感冒藥又買了暈車藥，正是
        這條規則最典型的情境。
        """
        pool = [v for v in existing if self._comparable(v)]
        for candidate in new_otc:
            if not self._comparable(candidate):
                pool.append(candidate)
                continue
            for other in pool:
                finding = find_class_stacking(
                    candidate.ingredients, other.ingredients, self._anticholinergics
                )
                if finding:
                    return (
                        candidate,
                        other,
                        (finding.new_ingredient, finding.existing_ingredient),
                    )
            pool.append(candidate)
        return None

    def _first_class_pair(
        self, new_otc: list[_DrugView], existing: list[_DrugView]
    ) -> Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]]:
        """回報第一組 ATC 類別配對（出血風險），形狀與其餘規則相同。

        這條規則補的是成分層級抓不到的東西：布洛芬配華法林成分不同、都不是
        抗膽鹼、也沒有中藥，前三條規則全部落空，但那是最重要的成藥 × 處方藥
        組合之一。實測抗血栓藥（B01A）490 張全是處方藥、0 張非處方，因此
        觸發側必然是成藥——完全落在既有的觸發邊界內。
        """
        pool = [v for v in existing if self._comparable(v)]
        for candidate in new_otc:
            if not self._comparable(candidate):
                pool.append(candidate)
                continue
            for other in pool:
                finding = find_class_pair(
                    candidate.atc_codes, other.atc_codes, self._class_pairs
                )
                if finding:
                    return candidate, other, (finding.new_label, finding.existing_label)
            pool.append(candidate)
        return None

    def _first_tcm_interaction(
        self, new_otc: list[_DrugView], existing: list[_DrugView]
    ) -> Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]]:
        """回報第一組中西藥交互作用，形狀與另外兩條規則相同。

        **兩個方向都要看**：新加入的是中藥而現有用藥是西藥（自費看中醫，家裡
        還有醫師開的藥），或新加入的是成藥而現有用藥是中藥（在吃中藥期間自己
        去買了感冒藥）。兩者一樣常見，只查一個方向會漏掉一半。

        比對池同樣包含這次提交裡先前處理過的藥——同一次掃描把中藥和成藥一起
        加進來是很自然的情形。

        中藥側的鍵取 `tcm_keys`（方名在最前面，其後是組成藥材）；西藥側取
        藥證庫解析出來的 `ingredients`。兩者由
        `app/services/safety/tcm_interaction.py` 的配對表橋接——那張表是衛福部
        維護的，不是我們推測出來的對應。
        """
        pool = list(existing)
        for candidate in new_otc:
            for other in pool:
                finding = self._tcm_pair(candidate, other) or self._tcm_pair(other, candidate)
                if finding is not None:
                    tcm_side, western_side, names = finding
                    # 回報時一律把「新加入的那個」放在前面：訊息要講的是
                    # 「你剛加入的這個，和你正在吃的那個」。
                    if tcm_side is candidate:
                        return candidate, other, names
                    return candidate, other, (names[1], names[0])
            pool.append(candidate)
        return None

    def _tcm_pair(
        self, tcm_side: _DrugView, western_side: _DrugView
    ) -> Optional[tuple[_DrugView, _DrugView, tuple[str, str]]]:
        """單一方向的判定：`tcm_side` 是中藥、`western_side` 是西藥時是否成立。"""
        if not tcm_side.is_tcm or not western_side.ingredients:
            return None
        if not self._comparable(western_side):
            return None
        finding = find_tcm_interaction(
            tcm_side.tcm_keys, western_side.ingredients, self._tcm_interactions
        )
        if not finding:
            return None
        return tcm_side, western_side, (finding.tcm_name, finding.western_ingredient)

    # ---- 資料 --------------------------------------------------------------

    async def _views(self, medication_ids: list[str]) -> list[_DrugView]:
        medications = await self._medication_repository.find_by_ids(medication_ids)
        return [self._to_view(m) for m in medications or []]

    async def _existing_views(
        self, patient_user_id: str, exclude_ids: set[str]
    ) -> list[_DrugView]:
        """當事人目前仍有效的其他用藥。

        直接查該使用者「啟用中且當日在效期內」的藥品，**不繞提醒規則**。
        之前是「提醒規則 → 藥品 id → 當日仍有效」，而 PRN（需要時）的藥從來
        不會掛在任何提醒上（見 `PrescriptionScanService._link_reminders`），
        於是「痛的時候才吃」的那盒止痛藥——成藥最典型的用法——在之後每一次
        比對裡都是隱形的；布洛芬配抗凝血劑這一組正好就漏在這裡。

        「仍有效」的判定沿用同一套日期窗：已停用或療程已結束的藥不該再參與
        比對，否則三個月前那盒感冒藥會永遠讓新藥觸發警報，而使用者無從讓它
        停下來。
        """
        date_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        medications = await self._medication_repository.list_active_by_user(
            patient_user_id, date_str
        )
        return [
            self._to_view(m)
            for m in medications or []
            if str(getattr(m, "id", "") or "") not in exclude_ids
        ]

    def _to_view(self, medication: Any) -> _DrugView:
        """把 `Medication` 補上藥證庫的分級、成分與劑型。

        `entry_by_license_number` 查無（使用者沒有確認證號、或藥證庫是尚未帶
        新欄位的舊版）時，成分為空、分級為空字串——`should_check` 對空字串回
        False，這筆藥因此既不觸發偵測也不會被誤判成非處方藥。
        """
        entry = None
        license_number = getattr(medication, "license_number", None)
        if license_number:
            entry = self._catalog_service.entry_by_license_number(license_number)
        # 中藥沒有藥證字號，走方名比對。只在西藥藥證庫查無成分時才試——藥證庫
        # 命中代表這是一張真實的西藥藥證，不該再拿去問中藥庫。
        tcm_keys: tuple[str, ...] = ()
        if self._tcm_catalog_service is not None and not getattr(entry, "ingredients", None):
            tcm_entry = self._tcm_catalog_service.match(getattr(medication, "name", "") or "")
            if tcm_entry is not None:
                tcm_keys = tcm_entry.interaction_keys
        return _DrugView(
            medication_id=str(getattr(medication, "id", "") or ""),
            name=getattr(medication, "name", "") or "",
            ingredients=tuple(getattr(entry, "ingredients", ()) or ()),
            atc_codes=tuple(getattr(entry, "atc_codes", ()) or ()),
            dosage_form=getattr(entry, "dosage_form", "") or "",
            drug_class=getattr(entry, "drug_class", "") or "",
            # 用途取食藥署仿單摘要，**不取 `Medication.indication`**——後者是
            # 使用者自己寫的備註，常帶病情原話（「睡不著吃這個」），與
            # `safety_flex` 頂端「原始提問不進推播」是同一條理由。
            indication=getattr(medication, "spc_indication_summary", None),
            tcm_keys=tcm_keys,
        )

    # ---- 通知 --------------------------------------------------------------

    async def _notify_family(
        self,
        patient_user_id: str,
        added: _DrugView,
        overlap: Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]],
        stacking: Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]] = None,
        tcm: Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]] = None,
        bleeding: Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]] = None,
    ) -> bool:
        """通報合格收件人，回傳是否真的送出給任何人。

        卡片講哪一組：有重複講重複的、有疊加講疊加的、都沒有講這次新增的第一
        個藥。收件人不因種類而異——都走 `otc_medication_added`，不新增
        `NotificationKind`：政策相同的兩個 kind 只是多一個沒有行為差異的旋鈕，
        而觸發點本來就同一個（長輩自行加入非處方藥後偵測到問題）。
        """
        recipients = await self._recipients(patient_user_id)
        if not recipients:
            return False

        finding = bleeding or overlap or stacking or tcm
        subject = finding[0] if finding else added
        patient_name = await self._patient_name(patient_user_id)
        sent = False
        for member_id in recipients:
            language, font_size = await self._display_prefs(member_id)
            flex = build_otc_family_flex(
                patient_name=patient_name,
                drug_name=subject.name,
                indication=subject.indication,
                existing_drug_name=finding[1].name if finding else None,
                shared_ingredients=overlap[2] if overlap else (),
                stacked_ingredients=stacking[2] if stacking else (),
                tcm_pair=tcm[2] if tcm else (),
                class_pair=bleeding[2] if bleeding else (),
                language=language,
                font_size=font_size,
            )
            if not fits(flex.contents.to_dict()):
                # 走既有的大小上限規則：超過就退回純文字，寧可少了版面也不要
                # 讓 LINE 整則退回、家人什麼都收不到。
                logger.warning("非處方藥通知卡超過大小上限，改以純文字送出")
                await self._replier.push_text(member_id, flex.alt_text)
            else:
                await self._replier.push_flex(member_id, flex)
            sent = True
        return sent

    async def _notify_patient(
        self,
        patient_user_id: str,
        finding: tuple[_DrugView, _DrugView, tuple[str, ...]],
        notified_family: bool,
        kind: str = "overlap",
    ) -> None:
        """只在偵測到重複或疊加時才發。

        沒有任何發現時不打擾當事人：他剛完成加入動作，再收一則「已新增」只是
        重複他剛看過的畫面。

        措辭 SHALL NOT 給劑量建議或指示停藥——系統不取代藥事人員的專業判斷，
        而「先別吃」在真正需要那顆藥的情況下本身就是傷害。疊加那則多講一句
        「走路要特別小心」：Beers Table 5 對這組列的風險就是跌倒與骨折，而
        那是當事人自己當下就能採取的唯一安全動作。中西藥那則則先破除「中藥
        溫和、可以配著吃」這個前提——不先講這句，後面導向藥師的話會被當成
        小題大作。

        `kind` 是 overlap／stacking／tcm，決定用哪一組文案。
        """
        new_drug, existing_drug, ingredients = finding
        language, _ = await self._display_prefs(patient_user_id)
        key = f"text.otc.patient.{kind}" if notified_family else f"text.otc.patient.{kind}_solo"
        await self._replier.push_text(
            patient_user_id,
            t(key, language).format(
                new_drug=new_drug.name,
                existing_drug=existing_drug.name,
                ingredients="、".join(ingredients),
            ),
        )

    async def _recipients(self, patient_user_id: str) -> list[str]:
        """這次通報該送給誰。

        當事人本人恆不在此清單內——他走 `_notify_patient` 那條，而且只在有
        重複時才收。若他同時是自己族譜裡的成員，這裡也要濾掉，否則會收到兩則。

        查詢失敗一律回空並記 log：對主流程 fail-open、對通報 fail-closed。
        """
        if self._authorization_service is None:
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                patient_user_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("收件人判定失敗，本次不通報家人：%s", type(exc).__name__)
            return []
        return [uid for uid in recipients or [] if uid and uid != patient_user_id]

    async def _patient_name(self, user_id: str) -> str:
        if not self._user_profile_service:
            return _FALLBACK_PATIENT_NAME
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return _FALLBACK_PATIENT_NAME
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return _FALLBACK_PATIENT_NAME

    async def _display_prefs(self, user_id: str) -> tuple[str, str]:
        """逐一取收件人自己的語言與字級。背景推播沒有 request context 可用。"""
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE
        settings: dict = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
        )
