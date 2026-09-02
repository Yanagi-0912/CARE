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
也包含這次提交裡先前處理過的藥。

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
from app.services.safety.ingredient_overlap import (
    IngredientWatchlist,
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

    @property
    def is_otc(self) -> bool:
        return should_check(self.drug_class)


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
        reminder_repository: Any,
        replier: _Replier,
        watchlist: IngredientWatchlist,
        local_action_forms: frozenset = frozenset(),
        authorization_service: Any = None,
        user_profile_service: Any = None,
    ) -> None:
        self._catalog_service = catalog_service
        self._medication_repository = medication_repository
        self._reminder_repository = reminder_repository
        self._replier = replier
        self._watchlist = watchlist
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
        new_otc = [v for v in added if v.is_otc]
        if not new_otc:
            # 全是處方藥、非成品藥或分級不明——這條通道整個不啟動，連「新增了
            # 什麼藥」的通知都不發。處方藥已經過醫師診斷與藥師調劑，再通知一次
            # 只是噪音，而通知量該與風險成正比。
            return

        existing = await self._existing_views(patient_user_id, set(added_medication_ids))
        overlap = self._first_overlap(new_otc, existing)

        # 家人先發、當事人後發：當事人那則的措辭取決於家人是否真的收到了
        # （沒有合格收件人時不能說「也讓家人幫你看一下」）。
        notified_family = await self._notify_family(patient_user_id, new_otc[0], overlap)
        if overlap is not None:
            await self._notify_patient(patient_user_id, overlap, notified_family)

    # ---- 偵測 --------------------------------------------------------------

    def _comparable(self, view: _DrugView) -> bool:
        """這筆藥要不要進成分比對。

        局部作用劑型（點眼、含漱等）排除：全身吸收量可忽略，這種重複沒有臨床
        意義。**排除只作用在比對上，不影響「新增了非處方藥」的通知**——家人
        仍然該知道家裡多了一盒不用處方就能買到的藥。
        """
        return bool(view.ingredients) and not is_local_action(
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
            pool.append(candidate)
        return None

    # ---- 資料 --------------------------------------------------------------

    async def _views(self, medication_ids: list[str]) -> list[_DrugView]:
        medications = await self._medication_repository.find_by_ids(medication_ids)
        return [self._to_view(m) for m in medications or []]

    async def _existing_views(
        self, patient_user_id: str, exclude_ids: set[str]
    ) -> list[_DrugView]:
        """當事人目前仍有效的其他用藥。

        走「提醒規則 → 藥品 id → 當日仍有效」而不是直接掃 medications：已停用
        或療程已結束的藥不該再參與比對，否則三個月前那盒感冒藥會永遠讓新藥
        觸發警報，而使用者無從讓它停下來。
        """
        reminders = await self._reminder_repository.list_reminders_by_user(patient_user_id)
        ids = sorted(
            {
                mid
                for reminder in reminders or []
                for mid in (getattr(reminder, "medication_ids", None) or [])
                if mid not in exclude_ids
            }
        )
        if not ids:
            return []
        date_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        medications = await self._medication_repository.find_active_by_ids(ids, date_str)
        return [self._to_view(m) for m in medications or []]

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
        return _DrugView(
            medication_id=str(getattr(medication, "id", "") or ""),
            name=getattr(medication, "name", "") or "",
            ingredients=tuple(getattr(entry, "ingredients", ()) or ()),
            dosage_form=getattr(entry, "dosage_form", "") or "",
            drug_class=getattr(entry, "drug_class", "") or "",
            # 用途取食藥署仿單摘要，**不取 `Medication.indication`**——後者是
            # 使用者自己寫的備註，常帶病情原話（「睡不著吃這個」），與
            # `safety_flex` 頂端「原始提問不進推播」是同一條理由。
            indication=getattr(medication, "spc_indication_summary", None),
        )

    # ---- 通知 --------------------------------------------------------------

    async def _notify_family(
        self,
        patient_user_id: str,
        added: _DrugView,
        overlap: Optional[tuple[_DrugView, _DrugView, tuple[str, ...]]],
    ) -> bool:
        """通報合格收件人，回傳是否真的送出給任何人。

        有重複時卡片講的是重複的那一組，沒有重複時講的是這次新增的第一個藥。
        """
        recipients = await self._recipients(patient_user_id)
        if not recipients:
            return False

        subject = overlap[0] if overlap else added
        patient_name = await self._patient_name(patient_user_id)
        sent = False
        for member_id in recipients:
            language, font_size = await self._display_prefs(member_id)
            flex = build_otc_family_flex(
                patient_name=patient_name,
                drug_name=subject.name,
                indication=subject.indication,
                existing_drug_name=overlap[1].name if overlap else None,
                shared_ingredients=overlap[2] if overlap else (),
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
        overlap: tuple[_DrugView, _DrugView, tuple[str, ...]],
        notified_family: bool,
    ) -> None:
        """只在偵測到重複時才發。

        沒有重複時不打擾當事人：他剛完成加入動作，再收一則「已新增」只是重複
        他剛看過的畫面。

        措辭 SHALL NOT 給劑量建議或指示停藥——系統不取代藥事人員的專業判斷，
        而「先別吃」在真正需要那顆藥的情況下本身就是傷害。
        """
        new_drug, existing_drug, ingredients = overlap
        language, _ = await self._display_prefs(patient_user_id)
        key = (
            "text.otc.patient.overlap"
            if notified_family
            else "text.otc.patient.overlap_solo"
        )
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
