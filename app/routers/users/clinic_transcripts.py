"""看診錄音的 API。

上傳是 multipart，但跟藥袋掃描不同：藥袋是同步辨識完才回，這裡立刻回一筆
`processing` 的紀錄，轉錄在背景跑。理由見 `app/models/clinic_transcript.py`
的 `RecordStatus`——十分鐘的門診錄音撐不過一個 HTTP 請求。

### 權限用 SENSITIVE

診間對話會帶到診斷、症狀、家族病史，敏感度至少和用藥適應症（已登記為 SENSITIVE）
相當。不用 PRIVATE 的理由是這個功能存在的目的就是讓沒去的家人看得到，
設成 PRIVATE 等於把功能關掉。

### `consent` 是必填，沒有預設值

衛福部《醫療機構醫療隱私維護規範》第二點要求診療過程錄音須先徵得對方同意。
表單一定要選過才送得出來，後端也不給預設值——一個有預設值的同意欄位，
等於沒有徵詢過也會存進一筆看起來徵詢過的紀錄。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)

from app.core.config import settings
from app.dependencies import (
    CurrentUser,
    get_clinic_transcript_service,
    get_current_user,
    get_family_authorization_service,
)
from app.models.clinic_transcript import ClinicVisitRecord
from app.services.clinic_transcript.service import ClinicTranscriptService
from app.services.family.family_authorization_service import FamilyAuthorizationService

logger = logging.getLogger(__name__)

router = APIRouter()

FORBIDDEN_READ = "您沒有查看這位家人看診紀錄的權限"
FORBIDDEN_WRITE = "您沒有為這位家人建立看診紀錄的權限"

_ALLOWED_CONSENT = ("doctor_agreed", "self_recap")

# 允許的音檔類型。瀏覽器 MediaRecorder 在 Android 多半給 webm、iOS 給 mp4／m4a，
# 兩邊都要收。
_ALLOWED_PREFIX = "audio/"
_EXTENSION_BY_TYPE = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


async def _authorize(
    authz: FamilyAuthorizationService,
    operator_id: str,
    target_user_id: str,
    action: str,
    forbidden_detail: str,
) -> None:
    """一律嚴格判定（`has_legacy_equivalent=False`）。

    看診錄音是 RBAC 之後才有的功能，沒有要保留的舊行為；沿用預設的影子模式
    「在族譜裡就放行」，會讓只有一般讀取權的家人看到整段診間對話。
    """
    if operator_id == target_user_id:
        return
    try:
        await authz.authorize(
            operator_id, target_user_id, "SENSITIVE", action, has_legacy_equivalent=False
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail=forbidden_detail) from exc
        raise


@router.post(
    "",
    response_model=ClinicVisitRecord,
    status_code=202,
    summary="上傳看診錄音",
    description=(
        "以 multipart 上傳一整段看診錄音，立刻回傳一筆 status=processing 的紀錄；"
        "轉錄、摘要與藥名標示在背景進行，完成後 status 變成 ready。"
        "音檔只落在暫存檔，轉錄結束就刪除，不會寫進資料庫。"
        "consent 必填：doctor_agreed 表示醫師同意錄診間對話，"
        "self_recap 表示改由就診者出診間後自己複述。"
    ),
)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    consent: str = Form(...),
    target_user_id: Optional[str] = Form(default=None),
    appointment_id: Optional[str] = Form(default=None),
    hospital_name: str = Form(default=""),
    department: str = Form(default=""),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClinicTranscriptService = Depends(get_clinic_transcript_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> ClinicVisitRecord:
    if consent not in _ALLOWED_CONSENT:
        raise HTTPException(
            status_code=422, detail="consent 必須是 doctor_agreed 或 self_recap"
        )
    if not file.content_type or not file.content_type.startswith(_ALLOWED_PREFIX):
        raise HTTPException(status_code=415, detail="僅接受音訊檔案")

    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id
    await _authorize(authz, operator_id, user_id, "WRITE", FORBIDDEN_WRITE)

    # 真正擋住過大請求體的是 app/core/upload_limits.py 掛的 ASGI middleware；
    # 這裡是最後一道防線，理由與藥袋掃描那支相同（UploadFile 繫結會先把整個
    # multipart body 讀完，路由層再檢查已經太晚）。
    audio = await file.read()
    if len(audio) > settings.CLINIC_RECORDING_MAX_BYTES:
        raise HTTPException(status_code=413, detail="錄音檔過大，請縮短錄音長度")
    if not audio:
        raise HTTPException(status_code=422, detail="錄音檔是空的")

    record = await service.start(
        user_id=user_id,
        created_by_user_id=operator_id,
        consent=consent,  # type: ignore[arg-type]
        appointment_id=appointment_id,
        hospital_name=hospital_name,
        department=department,
    )

    suffix = _EXTENSION_BY_TYPE.get(file.content_type.split(";")[0].strip(), ".m4a")
    # delete=False：背景工作在這個請求結束之後才讀它，交給 service.process()
    # 在 finally 裡刪。
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(audio)
        audio_path = Path(handle.name)

    background_tasks.add_task(service.process, record, audio_path)
    logger.info(
        "stage=clinic_upload record=%s consent=%s bytes=%d",
        record.id,
        consent,
        len(audio),
    )
    return record


@router.get(
    "",
    response_model=List[ClinicVisitRecord],
    summary="查詢看診紀錄列表",
    description="取得本人或指定家人的看診錄音紀錄，由新到舊；省略 target_user_id 即為本人。",
)
async def list_records(
    target_user_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClinicTranscriptService = Depends(get_clinic_transcript_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> List[ClinicVisitRecord]:
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id
    await _authorize(authz, operator_id, user_id, "READ", FORBIDDEN_READ)
    return await service.list_records(user_id, limit)


@router.get(
    "/{record_id}",
    response_model=ClinicVisitRecord,
    summary="查詢單筆看診紀錄",
    description="取得逐字稿、摘要與藥名標示。",
)
async def get_record(
    record_id: str,
    target_user_id: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClinicTranscriptService = Depends(get_clinic_transcript_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> ClinicVisitRecord:
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id
    await _authorize(authz, operator_id, user_id, "READ", FORBIDDEN_READ)
    record = await service.get_record(record_id, user_id)
    if record is None:
        # 不存在與不屬於這位使用者統一回 404，否則這支會變成探測他人
        # record_id 是否存在的管道（與藥袋草稿那支同一條理由）。
        raise HTTPException(status_code=404, detail="找不到這筆看診紀錄")
    return record


@router.delete(
    "/{record_id}",
    status_code=204,
    summary="刪除看診紀錄",
    description="立刻刪除，不等 30 天的保存期限到期。",
)
async def delete_record(
    record_id: str,
    target_user_id: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: ClinicTranscriptService = Depends(get_clinic_transcript_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> None:
    operator_id = current_user.line_user_id
    user_id = target_user_id or operator_id
    await _authorize(authz, operator_id, user_id, "WRITE", FORBIDDEN_WRITE)
    if not await service.delete_record(record_id, user_id):
        raise HTTPException(status_code=404, detail="找不到這筆看診紀錄")
