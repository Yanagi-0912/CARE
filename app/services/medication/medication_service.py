import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional
from fastapi import HTTPException

from app.models.family_tree import FamilyTree
from app.models.medication import (
    CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES,
    DEFAULT_MISFIRE_GRACE_MINUTES,
    DEFAULT_SLOT_TIMES,
    SLOT_DISPLAY_NAMES,
    TAIPEI_TZ,
    URGENT_AFTER_ANCHOR_MINUTES,
    CreateMedicationReminderRequest,
    CreateMedicationRequest,
    Medication,
    MedicationLog,
    MedicationReminder,
    MedicationReminderWithMedications,
    MedicationVisit,
    ReminderEntry,
    UpdateMedicationReminderRequest,
    derive_entry_fields,
    ensure_aware_utc,
)
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationRepository,
    MedicationReminderRepository,
)
from app.services.line_messaging.flex.medication_flex import (
    MedicationGroup,
    MedicationListEntry,
)
from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
)

logger = logging.getLogger(__name__)

# 證號 -> 對外縮圖 URL（查無縮圖回 None）。以純函式簽章注入而非直接依賴
# drug_appearance_image_service 模組，測試才能餵一個字典查表的假實作，
# 不必碰檔案系統；與 PrescriptionScanService、MedicationScheduler 同一條
# 慣例（見兩者的 _AppearanceImageResolver）。
_AppearanceImageResolver = Callable[[str], Optional[str]]


def _today_date_str() -> str:
    return _now_taipei().strftime("%Y-%m-%d")


def _now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def _today_at_taipei(now_taipei: datetime, hhmm: str) -> datetime:
    """把一個 HH:MM 時刻接上 `now_taipei` 所在的台北日期，組成帶時區的
    datetime。改排程對齊（`update_reminder`）與 `_suppress_stale_new_slot`
    都需要「規則某個時刻在今天對應到哪一個瞬間」，且必須與排程器展開
    `scheduled_at` 的基準（`datetime.now(TAIPEI_TZ)`）一致，否則算出來的
    時刻對不上已展開的紀錄，該對齊的沒對齊、該保留的反而被誤動。
    """
    return datetime.strptime(
        f"{now_taipei.strftime('%Y-%m-%d')} {hhmm}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=TAIPEI_TZ)


def _entry_medication_ids(entries: list) -> List[str]:
    """一批條目的藥品 id 聯集（不重複、保留出現順序）。建立與更新提醒時，
    驗證藥品歸屬（`_assert_medications_belong`）要用同一份聯集。"""
    ids: List[str] = []
    for entry in entries:
        for medication_id in entry.medication_ids:
            if medication_id not in ids:
                ids.append(medication_id)
    return ids


class MedicationService:
    """
    用藥提醒與日誌業務邏輯處理
    """

    def __init__(
        self,
        medication_repository=MedicationRepository,
        reminder_repository=MedicationReminderRepository,
        log_repository=MedicationLogRepository,
        appearance_image_resolver: _AppearanceImageResolver = resolve_drug_appearance_image_url,
        indication_service=None,
        misfire_grace_minutes: int = DEFAULT_MISFIRE_GRACE_MINUTES,
        clock: Callable[[], datetime] = _now_taipei,
        catalog_service=None,
        otc_alert_service=None,
    ) -> None:
        # 其餘方法沿用既有慣例，直接呼叫 repository 的 staticmethod；
        # 這裡額外開可注入的參數，給 get_user_reminders_with_medications 與
        # update_reminder 用——測試不需要碰資料庫，也不必用 monkeypatch 換掉
        # 整個 import（openspec 的測試規則明文禁止後者）。
        self._medication_repository = medication_repository
        self._reminder_repository = reminder_repository
        self._log_repository = log_repository
        self._appearance_image_resolver = appearance_image_resolver
        # 改排程到已經過去的時刻時要不要先註銷該時刻，門檻與排程器判定「錯過」
        # 用的是同一個值（見 DEFAULT_MISFIRE_GRACE_MINUTES）。開成參數只為了讓
        # 測試不必配合真實的 20 分鐘擺弄時間；正式路徑兩邊都用預設值。
        self._misfire_grace_minutes = misfire_grace_minutes
        # 台北時間的「現在」。可注入是為了讓改排程那段的測試能固定在一個時刻上
        # ——它同時要算「今天是哪一天」與「離現在多久」，跟著真實時鐘跑的話，
        # 測試在午夜前後會算出前一天的日期而飄紅。
        self._clock = clock
        # 選填：未注入時仿單欄位一律 None，前端只顯示藥袋讀到的適應症。
        self._indication_service = indication_service
        # 選填：手動新增時拿藥名去藥證庫比對（唯一命中才釘證號），以及
        # 新增後的相衝偵測。兩者都是旁路——未注入時手動新增照舊只存藥名。
        self._catalog_service = catalog_service
        self._otc_alert_service = otc_alert_service
        self._otc_alert_tasks: set[asyncio.Task] = set()

    async def create_reminders(
        self, creator_user_id: str, request: CreateMedicationReminderRequest
    ) -> List[MedicationReminder]:
        """
        為本人或家庭成員建立用藥提醒（支援勾選早/中/晚/睡前時段與日期區間）
        """
        target_user_id = request.user_id

        # 授權由呼叫端（router）經 FamilyAuthorizationService 判定：為他人建立
        # 提醒需要對該用藥者的 GENERAL 具備寫入權。這裡刻意**不再**手寫族譜
        # 檢查——「在族譜裡＝有權」正是本 change 要消滅的語意，留一份在這裡
        # 就會有人以為它還是授權依據，而它比矩陣寬。

        # 去重複但保留順序：同一次請求重複勾選同一個時段（例如手滑點兩下、
        # 或前端表單重複送出同一個 slot）不該被當成「兩個不同時段」各自通過
        # 下面的衝突檢查——那正是這個檢查要防止的事，重複的時段會在迴圈裡
        # 對同一個時段建立兩筆規則。
        slots = list(dict.fromkeys(request.slots))

        # 先把請求的每個時段都檢查過一輪，任一時段已有規則就整批擋下、
        # 不建立任何規則（spec「提醒規則與用藥對象」）。現況是靜默建第二筆，
        # 本 change 收緊成 409——否則詳細頁重複送出、或使用者手滑點兩次，
        # 就會在同一個時段留下兩筆規則，那個時段從此每天收到兩則推播。
        #
        # 這裡是「先讀後寫」（TOCTOU），不是像 find_or_create_reminder 那樣
        # 單一 document 的原子 upsert；兩個幾乎同時送出的建立請求仍可能都
        # 通過這個檢查、各自成功寫入同一個時段兩筆規則。這個機率遠低於
        # find_or_create_reminder 說明的情境（那邊完全沒有這道檢查），本
        # change 不在這裡另外補一套併發防護。
        existing = await self._reminder_repository.list_reminders_by_user(target_user_id)
        occupied_slots = {reminder.slot_type for reminder in existing}
        conflict = next((slot for slot in slots if slot in occupied_slots), None)
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"時段「{SLOT_DISPLAY_NAMES[conflict]}」已有用藥提醒",
            )

        start_date = request.start_date or _today_date_str()

        # 先把每個時段要建立的條目都算好、把全部條目的藥品歸屬一次驗證完，
        # 一筆規則都還沒寫入資料庫——不能像過去那樣邊驗證邊建立：若請求的
        # 後面某個時段驗證失敗，前面的時段已經建立成功，使用者收到 400 後
        # 重試整個請求，前面那個時段又會撞上剛剛才建立的規則而變成 409，
        # 這個端點就再也無法用來建立那個時段了（見 spec「提醒規則與用藥
        # 對象」「時段已有規則」）。
        entries_by_slot: dict[str, list[ReminderEntry]] = {}
        for slot in slots:
            # 該時段的 slot_entries 有給就以它為準（飯前／飯後拆批）；否則沿用
            # 既有的單一時刻行為，合成一個單一 none 條目（design 決策 1）。
            if request.slot_entries and slot in request.slot_entries:
                entries: list[ReminderEntry] = list(request.slot_entries[slot])
            else:
                scheduled_time = (
                    request.slot_times.get(slot)
                    if request.slot_times and slot in request.slot_times
                    else DEFAULT_SLOT_TIMES.get(slot, "08:00")
                )
                entries = [ReminderEntry(meal_timing="none", scheduled_time=scheduled_time)]
            entries_by_slot[slot] = entries

        await self._assert_medications_belong(
            target_user_id,
            _entry_medication_ids(
                [entry for entries in entries_by_slot.values() for entry in entries]
            ),
        )

        created_reminders: List[MedicationReminder] = []
        for slot in slots:
            reminder = MedicationReminder(
                creator_user_id=creator_user_id,
                user_id=target_user_id,
                slot_type=slot,
                entries=entries_by_slot[slot],
                start_date=start_date,
                end_date=request.end_date,
                enabled=True,
            )
            saved = await self._reminder_repository.create_reminder(reminder)
            created_reminders.append(saved)

        logger.info(
            f"已建立 {len(created_reminders)} 筆用藥提醒: creator={creator_user_id}, target={target_user_id}"
        )
        return created_reminders

    async def _assert_medications_belong(
        self, user_id: str, medication_ids: List[str]
    ) -> None:
        """驗證一批藥品 id 全部屬於 `user_id`（spec「EntryInput.medication_ids
        必須全部屬於該用藥者」）。空清單直接放行——沒有要關聯藥品的條目不需要
        查詢，也不該因為空清單而誤判成「有 id 缺席」。
        """
        if not medication_ids:
            return
        medications = await self._medication_repository.find_by_ids(medication_ids)
        owned_ids = {
            medication.id for medication in medications if medication.user_id == user_id
        }
        missing = [mid for mid in medication_ids if mid not in owned_ids]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"藥品 {'、'.join(missing)} 不屬於此用藥者",
            )

    async def get_user_reminders(
        self,
        user_id: str,
        requester_user_id: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> List[MedicationReminder]:
        """取得特定使用者的所有用藥提醒"""
        # 授權同樣由呼叫端經 FamilyAuthorizationService 判定（GENERAL 讀取權），
        # 這裡不再自行檢查族譜。`requester_user_id` 保留在簽章上供既有呼叫端
        # 相容，本方法不再依它做任何判斷。
        # 只在真的有人注入 collection 時才多帶這個關鍵字參數：既有呼叫端與既有
        # 測試（斷言呼叫簽名是 list_reminders_by_user(user_id) 這個既定形狀）
        # 完全不受影響，只有新加的 get_user_reminders_with_medications 會用到。
        if collection is not None:
            return await MedicationReminderRepository.list_reminders_by_user(
                user_id, collection=collection
            )
        return await MedicationReminderRepository.list_reminders_by_user(user_id)

    async def get_user_reminders_with_medications(
        self,
        user_id: str,
        requester_user_id: Optional[str] = None,
        reminder_collection: Optional[Any] = None,
    ) -> List[MedicationReminderWithMedications]:
        """取得特定使用者的用藥提醒，並把每筆規則的 medication_ids 解析成完整的藥品清單。

        LIFF 的用藥提醒頁要顯示藥名（尤其是藥袋辨識建立的藥），不能只給一串 id
        讓前端逐筆再查一次——那是 N 次不必要的往返。這裡把所有規則用到的
        medication_ids 併成一次 `find_by_ids` 查詢，查完再按規則分回去。

        用 find_by_ids 而非 find_active_by_ids：這是使用者自己管理藥品的畫面，
        停用或已過療程的藥仍要看得到（才能重新啟用），推播才需要過濾成當下有效。
        """
        reminders = await self.get_user_reminders(
            user_id, requester_user_id, collection=reminder_collection
        )

        all_medication_ids = sorted(
            {mid for reminder in reminders for mid in reminder.medication_ids}
        )
        medications = await self._medication_repository.find_by_ids(all_medication_ids)
        # 縮圖 URL 不是資料庫裡存的值，是讀取當下依證號現算的（見
        # Medication.thumbnail_url 的欄位註解）——每個藥品只算一次，不論它
        # 掛在幾筆提醒規則底下，因為這裡是先建好 id -> 藥品的查表，下面組
        # 每筆提醒的 medications 清單時只是查表，不會重複呼叫解析器。
        medications_by_id = {
            medication.id: medication.model_copy(
                update={
                    "thumbnail_url": self._resolve_thumbnail(medication),
                    **self._resolve_indication(medication),
                }
            )
            for medication in medications
        }

        return [
            MedicationReminderWithMedications(
                **reminder.model_dump(by_alias=True),
                medications=[
                    medications_by_id[mid]
                    for mid in reminder.medication_ids
                    if mid in medications_by_id
                ],
            )
            for reminder in reminders
        ]

    def _resolve_indication(self, medication) -> dict:
        """依證號解析仿單適應症，回傳要覆寫到 Medication 上的欄位。

        沒有注入仿單服務、證號未確定、或查無該藥證時一律回 None——證號不確定
        代表不知道是哪一張藥證，顯示的適應症就可能屬於另一顆藥（與「證號不
        確定時不得顯示藥丸照片」同一條安全邊界）。

        摘要與原文都帶出去：前端有摘要時顯示摘要、可展開原文；摘要為空時
        直接顯示原文（spec 的「摘要缺席時的降級」）。
        """
        if self._indication_service is None:
            return {"spc_indication": None, "spc_indication_summary": None}
        found = self._indication_service.lookup(
            getattr(medication, "license_number", None)
        )
        if found is None:
            return {"spc_indication": None, "spc_indication_summary": None}
        return {
            "spc_indication": found.text,
            # 空字串（不需要摘要／產不出合格摘要）一律收斂成 None，讓前端
            # 只判斷一種「沒有摘要」的表示。
            "spc_indication_summary": found.summary or None,
        }

    def _resolve_thumbnail(self, medication: Medication) -> Optional[str]:
        """證號已確定時才嘗試解析縮圖 URL。

        與 MedicationScheduler._resolve_thumbnail 同一條規則：`license_number`
        為空 SHALL NOT 顯示照片（spec「證號不確定時不得顯示藥丸照片」），把關
        必須設在這裡，不能指望解析器自己判斷「這個證號是不是已經確定」，它只
        認檔案存不存在。解析本身出例外不能讓整批查詢連坐失敗，退化成沒有
        縮圖、文字列照常呈現（spec「照片缺席時的降級」）。
        """
        if not medication.license_number:
            return None
        try:
            return self._appearance_image_resolver(medication.license_number)
        except Exception:
            logger.exception(
                "[MedicationService] Failed to resolve drug appearance thumbnail "
                "for medication %s",
                medication.id,
            )
            return None

    async def get_reminder(self, reminder_id: str) -> MedicationReminder:
        """取得單筆提醒，供呼叫端在授權判定之前得知其用藥者是誰。

        授權需要「目標資料的擁有者」這項輸入，而提醒的擁有者是 `user_id`。
        呼叫端拿不到它就只能改用 `creator_user_id`——那正是要避免的後門。
        """
        reminder = await self._reminder_repository.get_reminder_by_id(reminder_id)
        if not reminder:
            raise HTTPException(status_code=404, detail="找不到該用藥提醒")
        return reminder

    async def get_creator_reminders(self, creator_user_id: str) -> List[MedicationReminder]:
        """取得創立者為家人或自己產生的所有用藥提醒"""
        return await MedicationReminderRepository.list_reminders_by_creator(creator_user_id)

    async def update_reminder(
        self, creator_user_id: str, reminder_id: str, request: UpdateMedicationReminderRequest
    ) -> MedicationReminder:
        """更新用藥提醒 (時段、時間、起訖日期、啟動狀態)"""
        reminder = await self._reminder_repository.get_reminder_by_id(reminder_id)
        if not reminder:
            raise HTTPException(status_code=404, detail="找不到該用藥提醒")

        # 授權由呼叫端經 FamilyAuthorizationService 判定，對象是該提醒的
        # **用藥者**。`creator_user_id` 僅為來源紀錄，SHALL NOT 構成授權依據：
        # 以建立者作為永久依據，等於任何曾經有權建立的人在權限被收回之後仍
        # 保有對既有資料的控制——而「收回權限」正是這套授權存在的目的。

        # `exclude_unset` 而非 `exclude_none`：兩者對「沒帶的欄位」行為相同
        # （都不會出現在 update_data 裡），差別在「有帶且是 null」。先前用
        # exclude_none 時 null 在這裡就被濾掉，使用者一旦設過 end_date 就永遠
        # 改不回「長期」，UI 只能反過來擋住這個操作；改成 exclude_unset 之後
        # 清空結束日期終於有辦法表達。
        update_data = request.model_dump(exclude_unset=True)

        # 代價是「明確送 null」對每個欄位都成立了，所以要自己界定哪些欄位的
        # null 有意義。scheduled_time 被寫成 null 時排程器的 strptime 會拋錯
        # 並被 except 吞掉——那筆提醒從此永遠不會觸發，且沒有任何錯誤回饋，
        # 是最糟的失敗方式（靜默地不再提醒吃藥）。寧可在這裡擋成 400。
        illegal_nulls = sorted(
            key
            for key, value in update_data.items()
            if value is None and key not in UpdateMedicationReminderRequest.NULLABLE_FIELDS
        )
        if illegal_nulls:
            raise HTTPException(
                status_code=400,
                detail=f"以下欄位不接受空值：{'、'.join(illegal_nulls)}",
            )

        # 改時段要先確認目標時段還空著。「同一位使用者的同一個時段永遠只該有
        # 一份規則」是排程器不重複推播的前提，而 `{user_id, slot_type}` 上刻意
        # 沒有 unique index（舊資料可能已有重複，建索引會讓應用起不來，見
        # MedicationReminderRepository.find_or_create_reminder）。資料庫不擋，
        # 改時段這條路徑就得自己擋——否則把早上改成晚上之後，晚上有兩份規則，
        # 那個時段從此每天推兩則。
        new_slot = update_data.get("slot_type")
        if new_slot and new_slot != reminder.slot_type:
            siblings = await self._reminder_repository.list_reminders_by_user(reminder.user_id)
            if any(other.slot_type == new_slot and other.id != reminder_id for other in siblings):
                raise HTTPException(
                    status_code=409,
                    detail="該時段已有另一筆用藥提醒，請先刪除或改用其他時段",
                )

        # 規則現有多個條目時，一個 scheduled_time 不知道要對應哪一個時刻
        # ——這種情況一律要求改用 entries 整份更新（spec「提醒時間格式驗證」）。
        # 判斷用的是**改動前**的條目數，不看這次請求帶不帶 entries。
        # 注意：這條擋的只是「規則本來就有多個條目」的歧義；請求同時帶了
        # entries 與 scheduled_time 並不會一律被擋下——下面 `"entries" in
        # update_data` 分支優先於 `scheduled_time` 分支，若改動前只有一個
        # 條目，兩者同時出現時 `derive_entry_fields(request.entries)` 會直接
        # 蓋掉這裡先寫入 update_data 的 scheduled_time，不會另外報錯。
        if "scheduled_time" in update_data and len(reminder.entries) > 1:
            raise HTTPException(
                status_code=400,
                detail="此提醒有多個服藥時機，請改用詳細設定調整時間",
            )

        # entries／scheduled_time 兩者都只是「使用者想改哪個時刻／哪些藥」的
        # 輸入，實際要送進資料層的是 derive_entry_fields 算出的四個派生欄位
        # ——entries 與 scheduled_time／timeout_anchor_time／medication_ids
        # 不能分開寫，否則兩者會不同步（design 決策 2）。
        if "entries" in update_data:
            # 整份取代：先驗證這批條目的藥品全部屬於這筆提醒的用藥者，再展開
            # 派生欄位並移除原始的 entries 輸入鍵。
            await self._assert_medications_belong(
                reminder.user_id, _entry_medication_ids(request.entries)
            )
            update_data.pop("entries", None)
            update_data.update(derive_entry_fields(request.entries))
        elif "scheduled_time" in update_data:
            # 只帶 scheduled_time 且規則只有一個條目（多條目已在上面擋掉）：
            # 同步改寫該條目的時刻，讓 entries 與派生欄位不會此後互相矛盾。
            rewritten_entry = reminder.entries[0].model_copy(
                update={"scheduled_time": update_data["scheduled_time"]}
            )
            update_data.update(derive_entry_fields([rewritten_entry]))

        updated = await self._reminder_repository.update_reminder(reminder_id, update_data)
        if not updated:
            raise HTTPException(status_code=500, detail="更新用藥提醒失敗")

        # 關閉規則只讓排程器「明天起」不再展開；當天已經展開、還沒確認的那筆紀錄
        # 不受影響，會繼續走 T+20 催促與 T+30 家屬逾時警報（三個階段都只查 log，
        # 不回頭確認規則現在還開不開）。使用者關掉之後照樣被催、家人照樣收到他漏
        # 吃藥的通知，看起來就像關閉這個功能根本沒作用。所以在關閉的當下就把那些
        # 紀錄註銷，讓後續推播停在這裡。
        #
        # 註銷排在更新成功之後：更新失敗時規則其實還是開著的，不該把當天的紀錄
        # 作廢。判斷用 `is False` 而不是 falsy——`enabled` 沒帶時是 None，那種請求
        # （例如只改時間）不能順手把當天的紀錄一起註銷。
        if request.enabled is False:
            cancelled = await self._log_repository.cancel_pending_by_reminder(reminder_id)
            if cancelled:
                logger.info(
                    "[MedicationService] 關閉提醒 %s，註銷當日未確認的執行紀錄 %d 筆",
                    reminder_id,
                    cancelled,
                )
        elif (
            updated.slot_type != reminder.slot_type
            or updated.scheduled_time != reminder.scheduled_time
        ):
            # 改排程與關閉是同一個問題的兩種形態：當日已展開的紀錄是規則在展開
            # 當下的快照，三階推播只讀紀錄、不回頭確認規則現在長什麼樣。08:05 把
            # 「早 08:00」改成「中 12:00」，08:20 仍會催「早 服藥未確認」、08:30
            # 家屬仍會收到他漏吃早上藥的警報——那個時段已經不存在了。
            #
            # 用 `updated` 與改動前的 `reminder` 逐欄比對，而不是看 update_data
            # 有沒有帶那個 key：把欄位原值重送一次不是改動，不該連帶動到紀錄。
            #
            # 日界與時刻的基準必須與排程器展開時一致（`process_ticks` 以
            # `datetime.now(TAIPEI_TZ)` 為準），否則算出來的時刻對不上已展開的
            # `scheduled_at`，該註銷的沒註銷、該保留的反而被註銷。
            now_taipei = self._clock()
            new_scheduled_at = _today_at_taipei(now_taipei, updated.scheduled_time)
            # 一併算出新的逾時錨點時刻，隨同這次對齊一起帶給
            # resync_pending_by_reminder——多條目規則若這次同時改了最早與最晚
            # 時刻，當日紀錄的 urgent_at／timeout_at 不該停在舊的最晚時刻上。
            # 只帶最早時刻改變、最晚時刻沒變時，這裡算出的值與紀錄原值相同，
            # repository 端的 $ne 比對本來就不會命中，無害。
            new_anchor_at = _today_at_taipei(now_taipei, updated.timeout_anchor_time)
            cancelled, retagged = await self._log_repository.resync_pending_by_reminder(
                reminder_id,
                scheduled_at=new_scheduled_at,
                slot_type=updated.slot_type,
                urgent_at=new_anchor_at + timedelta(minutes=URGENT_AFTER_ANCHOR_MINUTES),
                timeout_at=new_anchor_at + timedelta(minutes=CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES),
            )
            if cancelled or retagged:
                logger.info(
                    "[MedicationService] 提醒 %s 改排程為 %s %s，"
                    "註銷舊時刻的未確認紀錄 %d 筆、改標同時刻的 %d 筆",
                    reminder_id,
                    updated.slot_type,
                    updated.scheduled_time,
                    cancelled,
                    retagged,
                )

            await self._suppress_stale_new_slot(updated, new_scheduled_at, now_taipei)
        elif updated.timeout_anchor_time != reminder.timeout_anchor_time:
            # 最早時刻（scheduled_time／T+0）沒變、只有最晚時刻
            # （timeout_anchor_time）變了：排程器已展開的那筆紀錄的
            # scheduled_at 不用動，但 T+20／T+30 的推播基準要跟著改，否則會
            # 用舊的最晚時刻催促或通知家屬逾時（design 決策 5 第二種情形；
            # spec「只把飯後時間往後移」）。走的是同一支就地改標函式，只是
            # 這次沒有時刻不同的紀錄要註銷。
            now_taipei = self._clock()
            scheduled_at = _today_at_taipei(now_taipei, updated.scheduled_time)
            anchor_at = _today_at_taipei(now_taipei, updated.timeout_anchor_time)
            cancelled, retagged = await self._log_repository.resync_pending_by_reminder(
                reminder_id,
                scheduled_at=scheduled_at,
                slot_type=updated.slot_type,
                urgent_at=anchor_at + timedelta(minutes=URGENT_AFTER_ANCHOR_MINUTES),
                timeout_at=anchor_at + timedelta(minutes=CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES),
            )
            if cancelled or retagged:
                logger.info(
                    "[MedicationService] 提醒 %s 只改了最晚時刻為 %s，"
                    "就地改寫當日未確認紀錄的 urgent_at／timeout_at，共 %d 筆",
                    # 記資料庫讀回的 id，不記請求路徑原樣傳進來的 reminder_id：
                    # 值相同（能走到這裡代表已用它查到規則），但不讓使用者輸入
                    # 直接進 log（日誌注入，SonarCloud pythonsecurity:S5145）。
                    updated.id,
                    updated.timeout_anchor_time,
                    retagged,
                )

        return updated

    async def _suppress_stale_new_slot(
        self, reminder: MedicationReminder, scheduled_at: datetime, now: datetime
    ) -> None:
        """改排程後，若新時刻今天已經過去太久，先為它寫下一筆 `cancelled`。

        排程器展開紀錄時只看規則現在的 `scheduled_time`，不知道那個時刻是幾分鐘
        前才被改成這樣的。晚上八點把「晚 18:00」改成「早 08:00」，下一輪 tick 就
        會為今天 08:00 展開一筆紀錄——超過 misfire grace，它會被記成 `missed` 且
        不推播，但仍會進「錯過時段的彙整通知」，家屬因此收到一則「他今天漏吃早上
        的藥」，而那一劑從來不存在。

        搶先寫一筆 `cancelled`，排程器的 `$setOnInsert` 就會變成 no-op（留下紀錄
        而不是指望它不要展開，理由同「關閉時段規則」：留著才擋得住同一天後續
        tick 重新展開）。該時刻若已經有紀錄——包括剛剛被改標成新時段的那筆——
        `$setOnInsert` 同樣不會覆寫，這裡因此只會在「本來就沒有紀錄」時才真的
        插入，正好是排程器會憑空生出假漏服的那個情形。

        門檻用 misfire grace 而不是「現在」：剛過去幾分鐘的改動仍讓排程器照常
        展開並立刻推播——使用者把時間往前挪一點，本來就可能是想現在被提醒，那
        則推播不該消失。超過 grace 才是整條 T+0／T+20／T+30 時序已經失去意義的
        情形（見 `DEFAULT_MISFIRE_GRACE_MINUTES`）。

        規則已停用時不寫：排程器根本不會為它展開任何東西，寫了只是噪音。

        任何失敗只記錄不往外拋：規則本身已經更新成功了，不該因為這筆防禦性的
        記帳而讓使用者看到一個失敗的儲存。
        """
        if not reminder.enabled:
            return

        # `now` 由呼叫端傳入而不是在這裡重讀時鐘：新時刻的日期就是用同一個
        # 「現在」算出來的，兩者若來自兩次讀秒，跨午夜的那一瞬間會拿今天的門檻
        # 去比昨天的時刻。
        misfire_cutoff = now - timedelta(minutes=self._misfire_grace_minutes)
        if scheduled_at >= misfire_cutoff:
            return

        try:
            _, created = await self._log_repository.upsert_log(
                MedicationLog(
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    alert_notify_user_id=reminder.creator_user_id,
                    slot_type=reminder.slot_type,
                    scheduled_at=scheduled_at,
                    # 與排程器的 T+30 對齊。`cancelled` 的紀錄不會被任何推播階段
                    # 挑中，這個值實際上不會被讀到，但欄位是必填的。
                    timeout_at=scheduled_at
                    + timedelta(minutes=CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES),
                    status="cancelled",
                )
            )
        except Exception:
            logger.exception(
                "[MedicationService] 提醒 %s 改排程後，無法為已過去的新時刻 %s 預先註銷",
                reminder.id,
                scheduled_at.isoformat(),
            )
            return

        if created:
            logger.info(
                "[MedicationService] 提醒 %s 的新時刻 %s 今日已過（超過 %d 分鐘的補推期限），"
                "預先註銷，今日不補提醒",
                reminder.id,
                scheduled_at.isoformat(),
                self._misfire_grace_minutes,
            )

    async def delete_reminder(self, creator_user_id: str, reminder_id: str) -> bool:
        """刪除用藥提醒"""
        reminder = await self._reminder_repository.get_reminder_by_id(reminder_id)
        if not reminder:
            raise HTTPException(status_code=404, detail="找不到該用藥提醒")

        # 授權由呼叫端判定（同 update_reminder）。

        deleted = await self._reminder_repository.delete_reminder(reminder_id)

        # 刪除與關閉對當日紀錄的後果相同（見 update_reminder 的說明）：三階推播
        # 只查紀錄、不回頭確認規則還在不在，規則刪掉之後當天已展開、還沒確認的
        # 那筆仍會走完 T+20 催促與 T+30 家屬逾時警報——使用者刪了提醒，家人卻
        # 收到他漏吃的通知。刪除成功後才註銷：刪除失敗代表規則還在，紀錄該留。
        if deleted:
            cancelled = await self._log_repository.cancel_pending_by_reminder(reminder_id)
            if cancelled:
                logger.info(
                    "[MedicationService] 刪除提醒 %s，註銷當日未確認的執行紀錄 %d 筆",
                    reminder_id,
                    cancelled,
                )
        return deleted

    async def confirm_medication(
        self,
        log_id: str,
        user_id: str,
        medication_id: Optional[str] = None,
        taken_at: Optional[datetime] = None,
    ) -> MedicationLog:
        """確認用藥完成（spec「逐藥確認」「服藥確認」）。

        `taken_at`：算在哪一刻服藥，不給就是現在。只有在聊天裡回報
        （`MedicationReportService`）會給值——使用者說「我 12 點吃了」時，
        12:00 才是事實，而按下按鈕的現在只是他想起來的時刻。照實存下去也表示
        拖了四小時那一頓不會被拉霸算成準時（`reminder_variants.outcome_of` 用
        `taken_at <= timeout_at` 判定），這正是我們要的。

        不帶 `medication_id`：整批確認（【全部已服用】），行為與本變更前相同
        ——直接轉 `taken`，並把當下有效的藥品全部寫進 `taken_medication_ids`，
        讓用藥歷史能一致地回答「那次吃了什麼」（design 決策 4）。

        帶 `medication_id`：逐藥確認。先無條件記錄這顆藥（`add_taken_medication`
        是 `$addToSet`，重複按同一顆是冪等的），再重算「這筆規則於 log 當日
        仍有效的藥品」是否已全數包含在 `taken_medication_ids` 裡——到齊才收尾
        成 `taken`，未到齊維持原狀態（`pending`／`missed`／`cancelled` 皆可能，
        逐藥確認不改變狀態機，只累積已確認的藥）。
        """
        log = await self._log_repository.get_log_by_id(log_id)
        if not log:
            raise HTTPException(status_code=404, detail="找不到用藥日誌紀錄")

        if log.user_id != user_id:
            raise HTTPException(status_code=403, detail="無權限確認此用藥紀錄")

        if medication_id is None:
            # 整批確認刻意不採用 `_expected_medication_ids` 文件所述「SHALL NOT
            # 吞例外」的原則——那條規則是為了逐藥確認的到齊判定設計的：expected
            # 若被悄悄吞成空清單，會讓「還差一顆」被誤判成「全部到齊」。但整批
            # 確認本來就是使用者明確按下【全部已服用】，不需要 expected 來決定
            # 要不要轉成 taken；expected 這裡只是拿來填 taken_medication_ids
            # 讓用藥歷史好看。查詢失敗時若讓整筆確認跟著失敗，log 會卡在
            # pending，家屬之後反而收到一次子虛烏有的漏吃藥警報——兩害相權，
            # 寧可 taken_medication_ids 這次是空的，也不要讓確認本身失敗。
            try:
                expected = await self._expected_medication_ids(log)
            except Exception:
                logger.exception(
                    "[MedicationService] 整批確認時查詢有效藥品失敗，log_id=%s，"
                    "taken_medication_ids 將寫入空清單但仍完成確認",
                    # 同上：記資料庫讀回的 log.id，不記請求原樣的 log_id。
                    log.id,
                )
                expected = []
            updated_log = await self._log_repository.mark_as_taken(
                log_id, taken_at=taken_at, taken_medication_ids=expected
            )
            if not updated_log:
                raise HTTPException(status_code=400, detail="更新用藥狀態失敗或該紀錄已完成")
            return updated_log

        updated_log = await self._log_repository.add_taken_medication(log_id, medication_id)
        if not updated_log:
            raise HTTPException(status_code=404, detail="找不到用藥日誌紀錄")

        expected = await self._expected_medication_ids(log)
        if set(expected) <= set(updated_log.taken_medication_ids):
            completed = await self._log_repository.mark_as_taken(log_id, taken_at=taken_at)
            if completed:
                return completed
        return updated_log

    async def _active_medications_for_log(self, log: MedicationLog) -> List[Medication]:
        """該筆用藥日誌對應規則、於 log 台北日期仍有效的藥品，依
        `reminder.medication_ids` 的順序回傳。

        供 `list_medication_names_for_log`（卡片藥名清單）與
        `_expected_medication_ids`（逐藥確認的到齊判定）共用同一段查詢——
        兩者都需要「這筆 log 的規則、當日仍有效的藥品」，只是後續用途不同。
        有效性判定用的是**log 自己的台北日期**（而非今天），因為確認可能
        發生在跨日之後（例如睡前那一劑拖到隔天凌晨才按），用今天的日期去篩
        會把當時仍有效、今天才結束療程的藥錯誤地濾掉；排程器的
        `_TickMedicationNameCache` 也是同一條規則，兩邊必須算出同一個答案。
        """
        reminder = await self._reminder_repository.get_reminder_by_id(log.reminder_id)
        if not reminder or not reminder.medication_ids:
            return []

        date_str = (
            ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")
        )
        medications = await self._medication_repository.find_active_by_ids(
            reminder.medication_ids, date_str
        )
        medication_by_id = {medication.id: medication for medication in medications}
        return [
            medication_by_id[mid]
            for mid in reminder.medication_ids
            if mid in medication_by_id
        ]

    async def _expected_medication_ids(self, log: MedicationLog) -> List[str]:
        """逐藥／整批確認判定「是否到齊」所用的藥品 id 集合：該規則於 log 台北
        日期仍有效的藥品，於確認當下重新計算（design 決策 4——不使用推播當時
        的快照，訊息送出後又加了一顆藥，舊訊息按完仍差一顆；藥品被停用則自動
        不再計入）。

        刻意 SHALL NOT 吞例外：這裡的結果直接餵給狀態轉換的判定，DB 抖動時若
        悄悄把 expected 當成空清單，任何一次確認都會被誤判成「全部到齊」而把
        紀錄錯誤標記為已服藥——寧可讓這次確認失敗，也不要留下錯誤的用藥紀錄。
        與 `list_medication_names_for_log`／`medication_groups_for_log` 那種
        純卡片呈現用途（失敗只退化成沒有藥品區塊）不是同一個風險等級。
        """
        medications = await self._active_medications_for_log(log)
        return [medication.id for medication in medications]

    async def medication_groups_for_log(self, log: MedicationLog) -> List[MedicationGroup]:
        """T+0／T+20 卡片依服藥時機分區的資料（spec「推播列出該時段應服藥品」、
        design 決策 6）。依 `MEAL_TIMING_ORDER` 排序（`reminder.entries` 本身
        已由模型驗證器排過序），只含當日仍有效、且尚未在
        `taken_medication_ids` 裡的藥；一個條目的藥全部確認完就不再出現
        （`items` 為空的分區直接跳過，呼叫端不必再判斷一次）。

        與 `list_medication_names_for_log` 同一個失敗策略：任何失敗都只記錄
        並回空清單，不往外拋——這是卡片的呈現資料，不是確認判定的輸入，出錯
        時卡片退回沒有藥品區塊的原樣即可。
        """
        try:
            reminder = await self._reminder_repository.get_reminder_by_id(log.reminder_id)
            if not reminder or not reminder.medication_ids:
                return []

            date_str = (
                ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")
            )
            medications = await self._medication_repository.find_active_by_ids(
                reminder.medication_ids, date_str
            )
            medication_by_id = {medication.id: medication for medication in medications}
            taken_ids = set(log.taken_medication_ids)

            groups: List[MedicationGroup] = []
            for entry in reminder.entries:
                items = [
                    (
                        mid,
                        MedicationListEntry(
                            name=medication_by_id[mid].name,
                            image_url=self._resolve_thumbnail(medication_by_id[mid]),
                        ),
                    )
                    for mid in entry.medication_ids
                    if mid in medication_by_id and mid not in taken_ids
                ]
                if items:
                    groups.append(
                        MedicationGroup(
                            meal_timing=entry.meal_timing,
                            scheduled_time=entry.scheduled_time,
                            items=items,
                        )
                    )
            return groups
        except Exception:
            logger.exception(
                "[MedicationService] 無法取得日誌 %s 的分區藥品清單，卡片將不顯示藥品區塊",
                log.id,
            )
            return []

    async def taken_names_for_log(self, log: MedicationLog) -> List[str]:
        """該筆 log 已確認藥品的藥名，依 `taken_medication_ids` 的順序。

        刻意用 `find_by_ids` 而非 `find_active_by_ids`——不做日期／啟用篩選：
        使用者按下確認之後，這顆藥可能之後才被停用，但「那次吃了什麼」是
        已經發生過的事實，不該因為藥品現在的狀態而從歷史紀錄裡消失。

        任何失敗都只記錄並回空清單，理由同 `list_medication_names_for_log`：
        這是卡片上的補充資訊，不該因為查不到藥名就讓使用者看到錯誤。
        """
        if not log.taken_medication_ids:
            return []
        try:
            medications = await self._medication_repository.find_by_ids(
                log.taken_medication_ids
            )
            medication_by_id = {medication.id: medication for medication in medications}
            return [
                medication_by_id[mid].name
                for mid in log.taken_medication_ids
                if mid in medication_by_id
            ]
        except Exception:
            logger.exception(
                "[MedicationService] 無法取得日誌 %s 已確認藥品的藥名清單",
                log.id,
            )
            return []

    async def list_medications(self, user_id: str) -> List[Medication]:
        """列出某位使用者的全部藥品（含停用者，`GET /medications?user_id=`，
        見 spec「藥品的列出與手動新增」），縮圖與適應症的解析比照
        `get_user_reminders_with_medications` 同一套規則——都是讀取當下才
        現算、不落地存進資料庫的欄位。
        """
        medications = await self._medication_repository.list_by_user(user_id)
        return [
            medication.model_copy(
                update={
                    "thumbnail_url": self._resolve_thumbnail(medication),
                    **self._resolve_indication(medication),
                }
            )
            for medication in medications
        ]

    async def create_manual_medication(
        self, creator_user_id: str, request: CreateMedicationRequest
    ) -> Medication:
        """以藥名手動新增藥品（`POST /medications`，見 spec「藥品的列出與手動
        新增」）。手動新增沒有外觀資料來源，`frequency_code` 一律歸類 `OTHER`
        ——臆測頻次會直接變成錯誤的服藥時間（見 `MedicationFrequencyCode` 的
        欄位註解），外觀欄位維持模型預設的空字串。

        藥證字號走與藥袋掃描同一條規則（`_verify_against_catalog`）：藥名在
        藥證庫**唯一**命中才釘上，多張候選一律留空——挑一張就是編造，而使用者
        在這條路徑上沒有候選清單可挑。釘上證號的意義不是外觀照片（手動新增
        沒有候選可對），是讓相衝偵測拿得到成分與 ATC：之前手動新增的藥永遠
        沒有證號，於是長輩自己輸入的「普拿疼」在任何規則裡都是隱形的。
        """
        name = request.name.strip()
        medication = Medication(
            user_id=request.user_id,
            created_by_user_id=creator_user_id,
            name=name,
            license_number=self._unique_license_for(name),
            source="manual",
            frequency_code="OTHER",
        )
        created = await self._medication_repository.create_one(medication)
        self._schedule_otc_alert(request.user_id, created.id)
        return created

    def _unique_license_for(self, name: str) -> Optional[str]:
        """藥名在藥證庫唯一命中時的證號；查無、多張候選或未注入藥證庫都是 None。

        比對本身不會拋錯（純字典查詢），這裡仍然包起來：手動新增是使用者正在
        等的同步路徑，藥證庫任何意外都不該讓「存一個藥名」失敗。
        """
        if self._catalog_service is None or not name:
            return None
        try:
            match = self._catalog_service.match(name)
        except Exception:  # noqa: BLE001 - 旁路，不影響新增
            logger.warning("[MedicationService] 手動新增時藥證庫比對失敗，證號留空")
            return None
        return match.license_number if match is not None else None

    def _schedule_otc_alert(self, patient_user_id: str, medication_id: Optional[str]) -> None:
        """把相衝偵測丟到背景，比照 `PrescriptionScanService._schedule_otc_alert`。

        使用者正在等新增回應，偵測要查藥證庫、查現有用藥、推播家人，串進同步
        路徑只會讓畫面變慢，而且對「新增成功了嗎」沒有貢獻。例外留在任務內，
        log 不帶藥名（用藥組合是病史線索）。
        """
        if self._otc_alert_service is None or not patient_user_id or not medication_id:
            return

        async def _run() -> None:
            try:
                await self._otc_alert_service.check(patient_user_id, [medication_id])
            except Exception:  # noqa: BLE001 - 背景旁路，例外不得逸散
                logger.warning("[MedicationService] 手動新增後的相衝偵測任務失敗")

        task = asyncio.create_task(_run())
        self._otc_alert_tasks.add(task)
        task.add_done_callback(self._otc_alert_tasks.discard)

    async def list_medication_names_for_log(self, log: MedicationLog) -> List[str]:
        """取得某筆用藥日誌「當次」的藥名清單，供推播／回覆的卡片顯示。

        排程器有自己的批次版本（`_TickMedicationNameCache`）——那是為了一個 tick
        內多筆 log 共用查詢；這裡走的是使用者按下確認的單筆路徑，沒有可攤提的
        對象，逐筆查兩次反而最省。查詢邏輯與 `_expected_medication_ids` 共用
        `_active_medications_for_log`（見該方法註解）。

        任何失敗都只記 log 並回傳空清單，不往外拋：這個查詢純粹是卡片上的補充
        資訊，呼叫端拿到空清單時卡片會退回沒有藥品區塊的原樣。使用者的用藥已經
        確認成功了，不該因為「查不到藥名」而讓他看到錯誤、或懷疑剛才那一下沒被
        記錄到。
        """
        try:
            medications = await self._active_medications_for_log(log)
            return [medication.name for medication in medications]
        except Exception:
            logger.exception(
                "[MedicationService] 無法取得日誌 %s 的藥名清單，卡片將不顯示藥品區塊",
                log.id,
            )
            return []

    async def get_user_visits(self, user_id: str) -> List[MedicationVisit]:
        """使用者的看診紀錄。

        彙整留在 repository（那是一次 aggregate），這裡只負責轉成模型——
        比照本服務其餘方法的分工。
        """
        rows = await self._medication_repository.list_visits(user_id)
        return [MedicationVisit(**row) for row in rows]
