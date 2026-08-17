import logging
from datetime import datetime
from typing import Any, Callable, List, Optional
from fastapi import HTTPException

from app.models.family_tree import FamilyTree
from app.models.medication import (
    DEFAULT_SLOT_TIMES,
    TAIPEI_TZ,
    CreateMedicationReminderRequest,
    Medication,
    MedicationLog,
    MedicationReminder,
    MedicationReminderWithMedications,
    UpdateMedicationReminderRequest,
    ensure_aware_utc,
)
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationRepository,
    MedicationReminderRepository,
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
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


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
    ) -> None:
        # 其餘方法沿用既有慣例，直接呼叫 repository 的 staticmethod；
        # 這裡額外開可注入的參數，給 get_user_reminders_with_medications 與
        # update_reminder 用——測試不需要碰資料庫，也不必用 monkeypatch 換掉
        # 整個 import（openspec 的測試規則明文禁止後者）。
        self._medication_repository = medication_repository
        self._reminder_repository = reminder_repository
        self._log_repository = log_repository
        self._appearance_image_resolver = appearance_image_resolver

    async def create_reminders(
        self, creator_user_id: str, request: CreateMedicationReminderRequest
    ) -> List[MedicationReminder]:
        """
        為本人或家庭成員建立用藥提醒（支援勾選早/中/晚/睡前時段與日期區間）
        """
        target_user_id = request.user_id

        # 若幫別人設定，驗證目標使用者是否在 creator 的家庭族譜內
        if creator_user_id != target_user_id:
            tree = await FamilyTreeRepository.get_by_user_id(creator_user_id)
            if not tree or not any(m.user_id == target_user_id for m in tree.family_members):
                raise HTTPException(status_code=400, detail="用藥對象必須是您的家庭成員")

        start_date = request.start_date or _today_date_str()
        created_reminders: List[MedicationReminder] = []

        for slot in request.slots:
            scheduled_time = (
                request.slot_times.get(slot)
                if request.slot_times and slot in request.slot_times
                else DEFAULT_SLOT_TIMES.get(slot, "08:00")
            )
            reminder = MedicationReminder(
                creator_user_id=creator_user_id,
                user_id=target_user_id,
                slot_type=slot,
                scheduled_time=scheduled_time,
                start_date=start_date,
                end_date=request.end_date,
                enabled=True,
            )
            saved = await MedicationReminderRepository.create_reminder(reminder)
            created_reminders.append(saved)

        logger.info(
            f"已建立 {len(created_reminders)} 筆用藥提醒: creator={creator_user_id}, target={target_user_id}"
        )
        return created_reminders

    async def get_user_reminders(
        self,
        user_id: str,
        requester_user_id: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> List[MedicationReminder]:
        """取得特定使用者的所有用藥提醒"""
        if requester_user_id and requester_user_id != user_id:
            tree = await FamilyTreeRepository.get_by_user_id(requester_user_id)
            if not tree or not any(m.user_id == user_id for m in tree.family_members):
                raise HTTPException(status_code=400, detail="對象必須是您的家庭成員")
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
                update={"thumbnail_url": self._resolve_thumbnail(medication)}
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

        if reminder.creator_user_id != creator_user_id and reminder.user_id != creator_user_id:
            raise HTTPException(status_code=403, detail="無權限修改此用藥提醒")

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

        return updated

    async def delete_reminder(self, creator_user_id: str, reminder_id: str) -> bool:
        """刪除用藥提醒"""
        reminder = await MedicationReminderRepository.get_reminder_by_id(reminder_id)
        if not reminder:
            raise HTTPException(status_code=404, detail="找不到該用藥提醒")

        if reminder.creator_user_id != creator_user_id and reminder.user_id != creator_user_id:
            raise HTTPException(status_code=403, detail="無權限刪除此用藥提醒")

        return await MedicationReminderRepository.delete_reminder(reminder_id)

    async def confirm_medication(self, log_id: str, user_id: str) -> MedicationLog:
        """確認用藥完成"""
        log = await MedicationLogRepository.get_log_by_id(log_id)
        if not log:
            raise HTTPException(status_code=404, detail="找不到用藥日誌紀錄")

        if log.user_id != user_id:
            raise HTTPException(status_code=403, detail="無權限確認此用藥紀錄")

        updated_log = await MedicationLogRepository.mark_as_taken(log_id)
        if not updated_log:
            raise HTTPException(status_code=400, detail="更新用藥狀態失敗或該紀錄已完成")
        return updated_log

    async def list_medication_names_for_log(self, log: MedicationLog) -> List[str]:
        """取得某筆用藥日誌「當次」的藥名清單，供推播／回覆的卡片顯示。

        排程器有自己的批次版本（`_TickMedicationNameCache`）——那是為了一個 tick
        內多筆 log 共用查詢；這裡走的是使用者按下確認的單筆路徑，沒有可攤提的
        對象，逐筆查兩次反而最省。兩邊的結果必須一致，所以有效性判定同樣以
        **log 自己的台北日期**（而非今天）為準，藥名順序同樣沿用
        `reminder.medication_ids` 的順序。

        任何失敗都只記 log 並回傳空清單，不往外拋：這個查詢純粹是卡片上的補充
        資訊，呼叫端拿到空清單時卡片會退回沒有藥品區塊的原樣。使用者的用藥已經
        確認成功了，不該因為「查不到藥名」而讓他看到錯誤、或懷疑剛才那一下沒被
        記錄到。
        """
        try:
            reminder = await MedicationReminderRepository.get_reminder_by_id(
                log.reminder_id
            )
            if not reminder or not reminder.medication_ids:
                return []

            date_str = (
                ensure_aware_utc(log.scheduled_at)
                .astimezone(TAIPEI_TZ)
                .strftime("%Y-%m-%d")
            )
            medications = await self._medication_repository.find_active_by_ids(
                reminder.medication_ids, date_str
            )
            name_by_id = {medication.id: medication.name for medication in medications}
            return [
                name_by_id[mid]
                for mid in reminder.medication_ids
                if mid in name_by_id
            ]
        except Exception:
            logger.exception(
                "[MedicationService] 無法取得日誌 %s 的藥名清單，卡片將不顯示藥品區塊",
                log.id,
            )
            return []

