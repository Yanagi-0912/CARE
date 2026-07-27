from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException

from app.dependencies import CurrentUser, get_current_user, get_medication_service
from app.models.medication import (
    CreateMedicationReminderRequest,
    MedicationLog,
    MedicationReminder,
    UpdateMedicationReminderRequest,
)
from app.services.medication.medication_service import MedicationService

router = APIRouter()


@router.post(
    "/reminders",
    response_model=List[MedicationReminder],
    summary="新增用藥提醒",
    description="為自己或家庭成員勾選時段 (早/中/晚/睡前) 並設定起訖日期以新增用藥提醒。",
)
async def create_reminders(
    req: CreateMedicationReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    return await service.create_reminders(
        creator_user_id=current_user.line_user_id, request=req
    )


@router.get(
    "/reminders",
    response_model=List[MedicationReminder],
    summary="查詢用藥提醒列表",
    description="取得個人或指定成員的用藥提醒列表。若未傳入 target_user_id 則回傳本人提醒。",
)
async def get_reminders(
    target_user_id: Optional[str] = Query(default=None, description="要查詢的使用者 LINE userId"),
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    user_id = target_user_id or current_user.line_user_id
    return await service.get_user_reminders(user_id=user_id)


@router.put(
    "/reminders/{reminder_id}",
    response_model=MedicationReminder,
    summary="修改用藥提醒",
    description="修改用藥提醒的時間、起訖日期或開關狀態。",
)
async def update_reminder(
    reminder_id: str,
    req: UpdateMedicationReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    return await service.update_reminder(
        creator_user_id=current_user.line_user_id,
        reminder_id=reminder_id,
        request=req,
    )


@router.delete(
    "/reminders/{reminder_id}",
    summary="刪除用藥提醒",
    description="刪除指定的用藥提醒設定。",
)
async def delete_reminder(
    reminder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    ok = await service.delete_reminder(
        creator_user_id=current_user.line_user_id, reminder_id=reminder_id
    )
    return {"ok": ok}


@router.post(
    "/confirm/{log_id}",
    response_model=MedicationLog,
    summary="確認完成服藥",
    description="用藥者點擊【已用藥】按鈕後呼叫此 API 更新日誌狀態為 taken。",
)
async def confirm_medication(
    log_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    return await service.confirm_medication(
        log_id=log_id, user_id=current_user.line_user_id
    )
