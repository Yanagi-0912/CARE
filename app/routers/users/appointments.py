"""掛號提醒 API（LIFF）。

與用藥提醒同一套授權慣例：跨使用者的讀寫一律經 FamilyAuthorizationService，
對象是**就診者**（`user_id`），不是建立者——`creator_user_id` 只是來源紀錄。
寫入一律用嚴格判定，見 `_authorize`。

403 的 detail 在這裡換成掛號專用的文案：授權服務的預設訊息寫的是分類與動作的
代號（GENERAL／WRITE），前端會把 detail 原樣顯示給使用者看。
"""

from contextlib import contextmanager
from typing import Iterator, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import (
    CurrentUser,
    get_appointment_service,
    get_current_user,
    get_family_authorization_service,
)
from app.models.appointment import (
    AppointmentListScope,
    AppointmentReminderPage,
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
FORBIDDEN_DELETE_ALL_DETAIL = "只有本人可以刪除全部歷史紀錄。"
DELETE_ALL_SCOPE_DETAIL = "一次刪除只適用於過去的掛號紀錄，請帶 scope=past。"

PAST_PAGE_DEFAULT_LIMIT = 20
PAST_PAGE_MAX_LIMIT = 50


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
    """寫入一律 `has_legacy_equivalent=False`（已拍板）。

    掛號整個功能都是 RBAC 導入之後才有的，沒有要保留的 legacy 行為；沿用預設的話，
    影子模式的「在族譜裡就放行」會讓只有讀取權的 MEMBER 新增、修改、刪除長輩的掛號。
    讀取維持預設：MEMBER 本來就有 GENERAL 讀取權，兩種判定的結果相同。
    """
    try:
        await authz.authorize(
            operator_id,
            target_user_id,
            "GENERAL",
            action,
            has_legacy_equivalent=action == "READ",
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail=forbidden_detail) from exc
        raise


async def _visible(
    authz: FamilyAuthorizationService,
    payload: List[AppointmentReminderResponse],
    operator_id: str,
    user_id: str,
) -> list:
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


@router.get(
    "/reminders",
    response_model=Union[AppointmentReminderPage, List[AppointmentReminderResponse]],
    summary="查詢掛號提醒列表",
    description=(
        "取得本人或指定家人的掛號提醒，省略 target_user_id 即為本人。"
        "帶 scope 時回傳 {items, next_cursor, total_count}：scope=upcoming 是沒有取消、"
        "當日還沒結束的門診，由早到晚、不分頁；scope=past 是當日已結束或已取消的門診，"
        "由新到舊、以 cursor 分頁（limit 預設 20、上限 50）。"
        "不帶 scope 時維持舊格式（陣列，由早到晚）：include_past=false（預設）只回傳"
        "當日還沒結束的門診，true 回傳全部。"
    ),
)
async def list_reminders(
    target_user_id: Optional[str] = Query(default=None, description="就診者的 LINE userId"),
    include_past: bool = Query(
        default=False,
        description="只在不帶 scope 時有效（舊格式）：是否包含「當日已結束」的門診",
    ),
    scope: Optional[AppointmentListScope] = Query(
        default=None, description="upcoming 或 past；帶了才用新的回應格式"
    ),
    limit: int = Query(
        default=PAST_PAGE_DEFAULT_LIMIT,
        ge=1,
        le=PAST_PAGE_MAX_LIMIT,
        description="只對 scope=past 有效",
    ),
    cursor: Optional[str] = Query(
        default=None, description="只對 scope=past 有效：上一頁回應的 next_cursor"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id

    if operator_id != user_id:
        await _authorize(authz, operator_id, user_id, "READ", FORBIDDEN_READ_DETAIL)

    if scope is None:
        # 舊格式。部署中的前端打的是 ?include_past=true，前端改用 scope 之後才能拿掉。
        reminders = await service.list_for_user(user_id, include_past=include_past)
        return await _visible(
            authz, [r.to_response() for r in reminders], operator_id, user_id
        )

    with _http_errors():
        reminders, next_cursor, total_count = await service.list_scope(
            user_id, scope, limit=limit, cursor=cursor
        )
    return AppointmentReminderPage(
        items=await _visible(
            authz, [r.to_response() for r in reminders], operator_id, user_id
        ),
        next_cursor=next_cursor,
        total_count=total_count,
    )


@router.post(
    "/reminders",
    response_model=AppointmentReminderResponse,
    summary="新增掛號提醒",
    description=(
        "為本人或家人建立掛號提醒。appointment_at 必須是帶 offset 的 ISO 8601，"
        "例如 2026-09-15T09:30:00+08:00，精確到分鐘（秒數會被捨去）。"
        "facility_id 可為 null；門診時間不以院所的 clinic_time 驗證。"
        "同一位就診者同一時間只能有一筆沒有取消的掛號，不論醫院或科別（409）。"
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
        "改門診時間會讓狀態回到 scheduled，三個推播階段重新排定；"
        "已到診或已取消的掛號不能改時間（409）。"
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
    "/reminders",
    summary="刪除全部歷史紀錄",
    description=(
        "一次刪除本人所有「過去」的掛號紀錄——當日已結束或已取消，與 GET scope=past "
        "同一個判定——即將到來的門診一筆都不動。scope 必須是 past，否則 400。"
        "只有本人可以呼叫：target_user_id 省略或等於本人才放行，帶了別人的 id 回 403。"
        "回傳 {\"deleted\": 實際刪除的筆數}，沒有可刪的也是 200。"
    ),
)
async def delete_past_reminders(
    target_user_id: Optional[str] = Query(default=None, description="省略或等於本人"),
    scope: Optional[str] = Query(default=None, description="必須是 past"),
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
):
    # 不設預設值、也不接受 past 以外的值：漏帶參數 SHALL NOT 變成刪掉全部掛號。
    if scope != "past":
        raise HTTPException(status_code=400, detail=DELETE_ALL_SCOPE_DETAIL)
    operator_id = current_user.line_user_id
    # 只看是不是本人，不經家庭授權（已拍板）：GUARDIAN、CAREGIVER、受委任者都不行。
    # 帶了別人的 id SHALL NOT 退回去刪操作者自己的紀錄——家屬帶著長輩的 id 呼叫時，
    # 「忽略參數、改刪自己的」會讓他在不知情的情況下刪光自己的就醫紀錄。空字串同樣
    # 算帶了別人的 id：那多半是前端沒取到長輩的 id，一樣不能落到操作者自己身上。
    if target_user_id is not None and target_user_id != operator_id:
        raise HTTPException(status_code=403, detail=FORBIDDEN_DELETE_ALL_DETAIL)
    return {"deleted": await service.delete_past(operator_id)}


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
        "已經是 departed 時冪等回傳；attended／missed／cancelled 或當日已結束回 409。"
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
        "已經是 attended 時冪等回傳；missed／cancelled 或當日已結束回 409。"
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


@router.post(
    "/reminders/{reminder_id}/cancel",
    response_model=AppointmentReminderResponse,
    summary="取消這次門診",
    description=(
        "scheduled／departed → cancelled：保留紀錄、停止所有尚未發出的推播，不發任何通知。"
        "本人或對就診者有 GENERAL 寫入權的家屬可按，不限門診當天，不需要 body。"
        "已經是 cancelled 時冪等回傳（保留第一位取消者）；attended、missed 或當日已結束回 409。"
    ),
)
async def cancel(
    reminder_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: AppointmentService = Depends(get_appointment_service),
):
    with _http_errors():
        reminder = await service.cancel(reminder_id, current_user.line_user_id)
    return reminder.to_response()
