from typing import List, Optional
from fastapi import APIRouter, Depends, File, Query, HTTPException, UploadFile

from app.core.config import settings
from app.dependencies import (
    CurrentUser,
    get_current_user,
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
    response_model=List[MedicationReminderWithMedications],
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
):
    user_id = target_user_id or current_user.line_user_id
    return await service.get_user_reminders_with_medications(
        user_id=user_id, requester_user_id=current_user.line_user_id
    )



@router.get(
    "/reminders/created",
    response_model=List[MedicationReminder],
    summary="查詢自己開立的用藥提醒",
    description=(
        "取得由本人開立的所有用藥提醒，含為自己與為家庭成員設定的。"
        "與 GET /reminders 的差別：那支查的是「誰要吃藥」，這支查的是「誰設定的」。"
    ),
)
async def get_created_reminders(
    current_user: CurrentUser = Depends(get_current_user),
    service: MedicationService = Depends(get_medication_service),
):
    return await service.get_creator_reminders(
        creator_user_id=current_user.line_user_id
    )


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
):
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
