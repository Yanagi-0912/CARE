from typing import List, Optional
from fastapi import APIRouter, Depends, File, Query, HTTPException, UploadFile

from app.core.config import settings
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_medication_service,
    get_prescription_scan_service,
    require_prescription_scan_enabled,
)
from app.models.medication import (
    CreateMedicationReminderRequest,
    MedicationLog,
    MedicationReminder,
    MedicationReminderWithMedications,
    UpdateMedicationReminderRequest,
)
from app.models.prescription import (
    CommitPrescriptionDraftRequest,
    PrescriptionCommitResult,
    PrescriptionDraft,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.medication.medication_service import MedicationService
from app.services.medication.prescription_ocr_service import PrescriptionScanError
from app.services.medication.prescription_scan_service import (
    DraftExpiredError,
    DraftNotFoundError,
    PrescriptionScanService,
    SlotsRequiredError,
    TargetNotInFamilyError,
)

router = APIRouter()

# 凡是回傳 MedicationReminder／Medication／MedicationLog 的端點都要帶
# response_model_by_alias=False。
#
# 這些模型的 id 欄位是 Field(alias="_id")，為了能直接用 Mongo document 建構；
# 但 alias 同時作用於輸入與輸出，而 FastAPI 序列化 response_model 預設
# by_alias=True，於是 id 會被送成 `_id`。LIFF 讀的是 reminder.id，拿到
# undefined，接著打 PUT/DELETE /reminders/undefined，後端查不到就回 404
# 「找不到該用藥提醒」——關閉與刪除提醒因此完全失效。
#
# 為什麼不在模型上加 serialization_alias="id" 一次解決：repository 的三處寫入
# （create_reminder、create_medications、用藥日誌 upsert 的 $setOnInsert）都靠
# model_dump(by_alias=True) 產出的 `_id` 來判斷要不要補 ObjectId。改了模型，
# 那些判斷會恆真，既有 id 被丟棄、還會在文件裡多留一個雜散的 `id` 欄位。
# 把修正留在 HTTP 邊界，資料層完全不動。

# 辨識失敗的三種原因映射到的 HTTP 狀態碼。unreadable／not_prescription 都是
# 「這張圖本身沒問題，但讀不出可用內容」，用同一個 4xx 家族；
# service_unavailable 是外部服務故障，用 503 讓語意更精確，也讓呼叫端
# 除了 body 裡的 reason 之外，多一個能直接看 status code 判斷的訊號。
_SCAN_FAILURE_STATUS = {
    "unreadable": 422,
    "not_prescription": 422,
    "service_unavailable": 503,
}


def _scan_failure_response(exc: PrescriptionScanError) -> HTTPException:
    """三種辨識失敗原因 SHALL NOT 收斂成同一則錯誤——reason 一律帶在
    body 裡，呼叫端才能分別給使用者「重拍」「換一張」「稍後再試」三種
    完全不同的下一步指示。"""
    return HTTPException(
        status_code=_SCAN_FAILURE_STATUS[exc.reason],
        detail={"reason": exc.reason, "message": str(exc)},
    )


@router.post(
    "/reminders",
    response_model=List[MedicationReminder],
    response_model_by_alias=False,  # 見檔頭說明：輸出鍵須為 id，不是 _id
    summary="新增用藥提醒",
    description="為自己或家庭成員勾選時段 (早/中/晚/睡前) 並設定起訖日期以新增用藥提醒。",
)
async def create_reminders(
    req: CreateMedicationReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    # 為他人建立提醒需要對該用藥者的 GENERAL 具備寫入權——MEMBER 只有讀。
    # 請求主體帶得出 user_id，SHALL NOT 因此構成任何允許的依據。
    await authz.authorize(
        current_user.line_user_id, req.user_id, "GENERAL", "WRITE"
    )
    return await service.create_reminders(
        creator_user_id=current_user.line_user_id, request=req
    )


@router.get(
    "/reminders",
    response_model=List[MedicationReminderWithMedications],
    response_model_by_alias=False,  # 見檔頭說明：輸出鍵須為 id，不是 _id
    summary="查詢用藥提醒列表",
    description=(
        "取得個人或指定成員的用藥提醒列表。若未傳入 target_user_id 則回傳本人提醒。"
        "每筆提醒額外附上 medications：由 medication_ids 解析出的完整藥品清單，"
        "供 LIFF 顯示藥名，不需要再為每個 id 各查一次。"
    ),
)
async def get_reminders(
    target_user_id: Optional[str] = Query(default=None, description="要查詢的使用者 LINE userId"),
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """取得用藥提醒。

    用藥資料本身是 GENERAL，但**適應症是 SENSITIVE**——它回答的是「這個人
    為什麼吃這個藥」。因此這是一支混合分類端點：只有 GENERAL 讀取權者拿到
    200 與完整的藥品、時段，適應症欄位為空，**不是 403**。回 403 會讓他連
    「長輩早上要吃三種藥」都不知道，而那本來就是他有權知道的。

    遮蔽會遞迴進 `medications`：少了那一步，外層看到該欄位自己登記為 GENERAL
    就整包放行，適應症會從巢狀結構裡漏出去。
    """
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id

    if operator_id == user_id:
        return await service.get_user_reminders_with_medications(
            user_id=user_id, requester_user_id=operator_id
        )

    await authz.authorize(operator_id, user_id, "GENERAL", "READ")
    reminders = await service.get_user_reminders_with_medications(
        user_id=user_id, requester_user_id=operator_id
    )
    return await authz.mask_response(
        [r.model_dump(by_alias=False) for r in reminders],
        "medication_reminder",
        operator_id,
        user_id,
    )



@router.get(
    "/reminders/created",
    response_model=List[MedicationReminder],
    response_model_by_alias=False,  # 見檔頭說明：輸出鍵須為 id，不是 _id
    summary="查詢自己開立的用藥提醒",
    description=(
        "取得由本人開立的所有用藥提醒，含為自己與為家庭成員設定的。"
        "與 GET /reminders 的差別：那支查的是「誰要吃藥」，這支查的是「誰設定的」。"
    ),
)
async def get_created_reminders(
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """查詢自己開立的提醒。**回應逐筆經過授權判定。**

    這支端點的篩選條件正是 `creator_user_id`，而建立者已不再構成授權依據。
    若不逐筆判定，一位被降級的使用者仍能從這裡看到他當初為長輩設的全部
    用藥——授權從前門關掉，卻留了這扇後門。
    """
    operator_id = current_user.line_user_id
    reminders = await service.get_creator_reminders(creator_user_id=operator_id)

    visible = []
    for reminder in reminders:
        if reminder.user_id == operator_id:
            visible.append(reminder)
            continue
        if await authz.can(operator_id, reminder.user_id, "GENERAL", "READ"):
            visible.append(reminder)
    return visible


@router.put(
    "/reminders/{reminder_id}",
    response_model=MedicationReminder,
    response_model_by_alias=False,  # 見檔頭說明：輸出鍵須為 id，不是 _id
    summary="修改用藥提醒",
    description="修改用藥提醒的時間、起訖日期或開關狀態。",
)
async def update_reminder(
    reminder_id: str,
    req: UpdateMedicationReminderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """修改提醒。授權對象是該提醒的**用藥者**，不是建立者。

    `creator_user_id` 只是來源紀錄：任何人 SHALL NOT 因曾建立某筆資料而自動
    保有其後續的寫入權，否則「收回權限」這件事就永遠做不到。
    """
    reminder = await service.get_reminder(reminder_id)
    await authz.authorize(
        current_user.line_user_id, reminder.user_id, "GENERAL", "WRITE"
    )
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
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """刪除提醒。授權對象同樣是用藥者，不是建立者（見 update_reminder）。"""
    reminder = await service.get_reminder(reminder_id)
    await authz.authorize(
        current_user.line_user_id, reminder.user_id, "GENERAL", "WRITE"
    )
    ok = await service.delete_reminder(
        creator_user_id=current_user.line_user_id, reminder_id=reminder_id
    )
    return {"ok": ok}


@router.post(
    "/confirm/{log_id}",
    response_model=MedicationLog,
    response_model_by_alias=False,  # 見檔頭說明：輸出鍵須為 id，不是 _id
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


# ── 藥袋辨識 ──────────────────────────────────────────────────────────
#
# 三支端點皆掛 require_prescription_scan_enabled：PRESCRIPTION_SCAN_ENABLED
# 關閉時整條路徑要表現得像不存在一樣，回 404，而不是回一個「功能未開放」的
# 提示——後者會洩漏「這個功能存在，只是還沒開」，違反關閉時的隱私意圖。


@router.post(
    "/prescription-scan",
    response_model=PrescriptionDraft,
    summary="上傳藥袋影像進行辨識",
    description=(
        "以 multipart 上傳一張藥袋影像，辨識完成後回傳待使用者核對的草稿。"
        "影像僅存在於處理這次請求的記憶體中，不寫入資料庫或檔案系統。"
        "辨識失敗時以 reason 區分「建議重拍」「不是藥袋」「服務暫時無法使用」，"
        "三者對使用者的下一步指示完全不同。"
    ),
    dependencies=[Depends(require_prescription_scan_enabled)],
)
async def scan_prescription(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PrescriptionScanService = Depends(get_prescription_scan_service),
):
    # 非影像的 content type 直接拒絕，連讀取都不必——省一次不必要的記憶體佔用。
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="僅接受影像檔案")

    # 真正擋住過大請求體的是 app/core/upload_limits.py 掛的 ASGI middleware：
    # FastAPI 的 UploadFile=File(...) 繫結會在這支函式執行之前就經由
    # request.form() 把整個 multipart body 讀完，路由層這時候不管怎麼檢查
    # 都已經太晚，攔不住任何東西（實測驗證過）。這裡的檢查只是最後一道
    # 防線：萬一走到這裡時 image_bytes 仍然超過上限，還是要在呼叫
    # service.scan() 之前擋下來，不讓一張過大的影像進到辨識服務。
    image_bytes = await file.read()
    if len(image_bytes) > settings.PRESCRIPTION_SCAN_MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="影像檔案過大，請重新拍攝或壓縮後再試")

    try:
        return await service.scan(
            image_bytes=image_bytes,
            mime_type=file.content_type,
            user_id=current_user.line_user_id,
        )
    except PrescriptionScanError as exc:
        raise _scan_failure_response(exc) from exc


@router.get(
    "/prescription-drafts/{draft_id}",
    response_model=PrescriptionDraft,
    summary="查詢辨識草稿",
    description="查詢先前掃描產生的草稿，供核對畫面重新載入時使用。",
    dependencies=[Depends(require_prescription_scan_enabled)],
)
async def get_prescription_draft(
    draft_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: PrescriptionScanService = Depends(get_prescription_scan_service),
):
    try:
        return await service.get_draft(draft_id, current_user.line_user_id)
    except DraftNotFoundError:
        # 不存在與不屬於自己統一回 404，不區分兩者——否則這支端點會變成
        # 探測他人 draft_id 是否存在的管道。
        raise HTTPException(status_code=404, detail="找不到草稿")


@router.post(
    "/prescription-drafts/{draft_id}/commit",
    response_model=PrescriptionCommitResult,
    summary="提交辨識草稿",
    description=(
        "使用者核對草稿後提交，依草稿內容建立藥品並關聯至對應時段的提醒。"
        "已成功提交過的草稿再次提交會回傳原本的建立結果，不會重複建立。"
    ),
    dependencies=[Depends(require_prescription_scan_enabled)],
)
async def commit_prescription_draft(
    draft_id: str,
    payload: CommitPrescriptionDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: PrescriptionScanService = Depends(get_prescription_scan_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """提交藥袋辨識草稿。

    授權在**建立任何藥品或規則之前**完成：提交會一次寫入多筆藥品與提醒，
    寫到一半才發現無權，留下的是半套資料。

    `payload.user_id` 是使用者在核對畫面上確認的用藥對象，帶得出它 SHALL NOT
    構成任何允許的依據——服務層的 `TargetNotInFamilyError` 仍在，是縱深防禦
    的第二道，但它的語意（在族譜裡）比矩陣寬，不能當作唯一的閘門。
    """
    await authz.authorize(
        current_user.line_user_id, payload.user_id, "GENERAL", "WRITE"
    )
    try:
        return await service.commit(draft_id, current_user.line_user_id, payload)
    except DraftNotFoundError:
        raise HTTPException(status_code=404, detail="找不到草稿")
    except DraftExpiredError:
        # 過期的草稿其辨識結果多半也已過時，拒絕提交並提示重新掃描，
        # 而不是讓使用者對著一份可能早就對不上藥袋現況的草稿送出。
        raise HTTPException(status_code=410, detail="草稿已過期，請重新掃描藥袋")
    except TargetNotInFamilyError:
        raise HTTPException(status_code=400, detail="用藥對象必須是您本人或您的家庭成員")
    except SlotsRequiredError as exc:
        raise HTTPException(
            status_code=400, detail=f"「{exc}」的頻次無法自動判斷時段，請手動指定"
        )
