import logging
from datetime import datetime
from typing import List, Optional
from fastapi import HTTPException

from app.models.family_tree import FamilyTree
from app.models.medication import (
    DEFAULT_SLOT_TIMES,
    TAIPEI_TZ,
    CreateMedicationReminderRequest,
    MedicationLog,
    MedicationReminder,
    UpdateMedicationReminderRequest,
)
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
)

logger = logging.getLogger(__name__)


def _today_date_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


class MedicationService:
    """
    用藥提醒與日誌業務邏輯處理
    """

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
        self, user_id: str, requester_user_id: Optional[str] = None
    ) -> List[MedicationReminder]:
        """取得特定使用者的所有用藥提醒"""
        if requester_user_id and requester_user_id != user_id:
            tree = await FamilyTreeRepository.get_by_user_id(requester_user_id)
            if not tree or not any(m.user_id == user_id for m in tree.family_members):
                raise HTTPException(status_code=400, detail="對象必須是您的家庭成員")
        return await MedicationReminderRepository.list_reminders_by_user(user_id)

    async def get_creator_reminders(self, creator_user_id: str) -> List[MedicationReminder]:
        """取得創立者為家人或自己產生的所有用藥提醒"""
        return await MedicationReminderRepository.list_reminders_by_creator(creator_user_id)

    async def update_reminder(
        self, creator_user_id: str, reminder_id: str, request: UpdateMedicationReminderRequest
    ) -> MedicationReminder:
        """更新用藥提醒 (時段時間、起訖日期、啟動狀態)"""
        reminder = await MedicationReminderRepository.get_reminder_by_id(reminder_id)
        if not reminder:
            raise HTTPException(status_code=404, detail="找不到該用藥提醒")

        if reminder.creator_user_id != creator_user_id and reminder.user_id != creator_user_id:
            raise HTTPException(status_code=403, detail="無權限修改此用藥提醒")

        update_data = request.model_dump(exclude_none=True)
        updated = await MedicationReminderRepository.update_reminder(reminder_id, update_data)
        if not updated:
            raise HTTPException(status_code=500, detail="更新用藥提醒失敗")
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

