"""掛號提醒 API（LIFF）。

與用藥提醒同一套授權慣例：跨使用者的讀寫一律經 FamilyAuthorizationService，
對象是**就診者**（`user_id`），不是建立者——`creator_user_id` 只是來源紀錄。

403 的 detail 在這裡換成掛號專用的文案：授權服務的預設訊息寫的是分類與動作的
代號（GENERAL／WRITE），前端會把 detail 原樣顯示給使用者看。
"""

from contextlib import contextmanager
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import (
    CurrentUser,
    get_appointment_service,
    get_current_user,
    get_family_authorization_service,
)
from app.models.appointment import (
    AppointmentReminderResponse,
    CreateAppointmentReminderRequest,
    UpdateAppointmentReminderRequest,
)
from app.services.appointment.appointment_service import (
    AppointmentError,
    AppointmentService,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)

router = APIRouter()

FORBIDDEN_READ_DETAIL = "您沒有權限查看這位使用者的掛號提醒。"
FORBIDDEN_WRITE_DETAIL = "您沒有權限替這位使用者設定掛號提醒。"


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except AppointmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _authorize(
    authz: FamilyAuthorizationService,
    operator_id: str,
    target_user_id: str,
    action: str,
    forbidden_detail: str,
) -> None:
    try:
        await authz.authorize(operator_id, target_user_id, "GENERAL", action)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail=forbidden_detail) from exc
        raise


@router.get(
    "/reminders",
    response_model=List[AppointmentReminderResponse],
    summary="查詢掛號提醒列表",
    description=(
        "取得本人或指定家人的掛號提醒，依門診時間由早到晚。省略 target_user_id 即為本人。"
        "include_past=false（預設）只回傳今天與未來的門診；true 回傳全部。"
    ),
)
async def list_reminders(
    target_user_id: Optional[str] = Query(default=None, description="就診者的 LINE userId"),
    include_past: bool = Query(
        default=False,
        description="是否包含「當日已結束」的門診。界線與排程器標記 missed 相同",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id

    if operator_id != user_id:
        await _authorize(authz, operator_id, user_id, "READ", FORBIDDEN_READ_DETAIL)

    reminders = await service.list_for_user(user_id, include_past=include_past)
    payload = [reminder.to_response() for reminder in reminders]
    if operator_id == user_id:
        return payload
    # 目前每個欄位都是 GENERAL，遮蔽不會拿掉任何東西；仍然走這一步，是為了讓日後
    # 新增的欄位預設不外流（fail-closed，見 FIELD_CLASSIFICATION）。
    return await authz.mask_response(
        [item.model_dump() for item in payload],
        "appointment_reminder",
        operator_id,
        user_id,
    )


@router.post(
    "/reminders",
    response_model=AppointmentReminderResponse,
    summary="新增掛號提醒",
    description=(
        "為本人或家人建立掛號提醒。appointment_at 必須是帶 offset 的 ISO 8601，"
        "例如 2026-09-15T09:30:00+08:00，精確到分鐘（秒數會被捨去）。"
        "facility_id 可為 null；門診時間不以院所的 clinic_time 驗證。"
    ),
)
async def create_reminder(
    req: CreateAppointmentReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    # 請求主體帶得出 user_id，SHALL NOT 因此構成任何允許的依據。
    await _authorize(
        authz, current_user.line_user_id, req.user_id, "WRITE", FORBIDDEN_WRITE_DETAIL
    )
    with _http_errors():
        reminder = await service.create(current_user.line_user_id, req)
    return reminder.to_response()


@router.put(
    "/reminders/{reminder_id}",
    response_model=AppointmentReminderResponse,
    summary="修改掛號提醒",
    description=(
        "部分更新：沒帶的欄位不動，帶了 null 的清空。可為 null 的欄位只有 facility_id、"
        "hospital_address、hospital_phone、doctor_name、serial_number、note。"
        "改門診時間會讓狀態回到 scheduled，三個推播階段重新排定。"
    ),
)
async def update_reminder(
    reminder_id: str,
    req: UpdateAppointmentReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    with _http_errors():
        reminder = await service.get(reminder_id)
    await _authorize(
        authz, current_user.line_user_id, reminder.user_id, "WRITE", FORBIDDEN_WRITE_DETAIL
    )
    with _http_errors():
        updated = await service.update(reminder_id, req)
    return updated.to_response()


@router.delete(
    "/reminders/{reminder_id}",
    summary="刪除掛號提醒",
    description="刪除指定的掛號提醒；尚未發出的推播一併不再發出。",
)
async def delete_reminder(
    reminder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    with _http_errors():
        reminder = await service.get(reminder_id)
    await _authorize(
        authz, current_user.line_user_id, reminder.user_id, "WRITE", FORBIDDEN_WRITE_DETAIL
    )
    with _http_errors():
        ok = await service.delete(reminder_id)
    return {"ok": ok}


@router.post(
    "/reminders/{reminder_id}/depart",
    response_model=AppointmentReminderResponse,
    summary="回報已出發",
    description=(
        "scheduled → departed。本人或對就診者有 GENERAL 寫入權的家屬可按。"
        "已經是 departed 時冪等回傳；attended／missed／cancelled 回 409。"
        "門診當天（或 T-1h，取較早者）之前回 409。"
    ),
)
async def depart(
    reminder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
):
    with _http_errors():
        reminder = await service.depart(reminder_id, current_user.line_user_id)
    return reminder.to_response()


@router.post(
    "/reminders/{reminder_id}/attend",
    response_model=AppointmentReminderResponse,
    summary="回報已到診",
    description=(
        "scheduled／departed → attended，並停止這次門診所有尚未發出的推播。"
        "已經是 attended 時冪等回傳；missed／cancelled 回 409。"
    ),
)
async def attend(
    reminder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
):
    with _http_errors():
        reminder = await service.attend(reminder_id, current_user.line_user_id)
    return reminder.to_response()
