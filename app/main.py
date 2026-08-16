from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routers.line.webhook import router as line_router
from app.routers.liff.auth import router as auth_router
from app.routers.system import router as system_router
from app.routers.users.upsert_users import router as profile_router
from app.routers.users.consultations import router as consultations_router
from app.core.cors import add_cors_middleware
from app.core.config import settings
from app.core.logging_setup import configure_logging
from app.core.upload_limits import MaxUploadSizeMiddleware
from app.dependencies import (
    get_consultation_service,
    get_chat_history_repository,
    get_line_replier,
    get_user_profile_service,
    preload_facility_name_index,
    start_medication_scheduler,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.medication_repository import MedicationLogRepository
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.safety_alert_repository import SafetyAlertRepository
from app.services.consultation.scheduler import (
    start_consultation_daily_summary_scheduler,
)
from app.services.rag.user_document_store import ensure_user_docs_indexes_on_startup

from app.routers.users.family_tree import router as family_tree_router
from app.routers.users.knowledge_reports import router as knowledge_reports_router
from app.routers.users.medical import router as medical_router
from app.routers.users.medications import router as medications_router
from app.routers.admin.knowledge_reports import router as admin_knowledge_reports_router
from app.routers.tts.tts import router as tts_router

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI lifespan 會在 yield 前執行 startup 邏輯。
    # 先確認 MongoDB 摘要 collection 有 TTL index，讓摘要只保留 7 天。
    await ConsultationRepository.ensure_indexes()
    await KnowledgeReportRepository.ensure_indexes()
    # 用藥 log 的 (reminder_id, scheduled_at) 唯一索引：多實例並存時，
    # 它是「同一個時段只有一份 log」的唯一保證，推播權搶佔才有意義。
    await MedicationLogRepository.ensure_indexes()
    # 藥袋辨識草稿的 TTL 索引：草稿以 PRESCRIPTION_DRAFT_TTL_MINUTES 為存活
    # 時間，交由資料庫自動清除，應用端不需要另外排程刪除。
    await PrescriptionDraftRepository.ensure_indexes()
    # 用藥風險通報的節流索引：(user_id, drug_key) 的唯一約束是「同一個藥在
    # 節流視窗內只通報一次」的唯一保證，通報權就是靠它原子取得；expires_at
    # 的 TTL 讓視窗自動過期，不需要應用端排程清除。索引與功能開關無關，
    # 先備好才能在開關打開的當下就是正確行為。
    await SafetyAlertRepository.ensure_indexes()
    await ensure_user_docs_indexes_on_startup()

    # 預載院所名稱索引，供判斷使用者說的「診所／醫院／藥局」是專名還是泛稱。
    # 放在啟動而非對話路徑：名稱集合約 512 KB，載入一次即可，
    # 且意圖判定是同步函式，不適合在其中做非同步查詢。
    await preload_facility_name_index()

    # 啟動每日諮詢摘要排程
    scheduler = start_consultation_daily_summary_scheduler(
        enabled=True,  # 啟動自動排程
        run_time=settings.CONSULTATION_DAILY_SUMMARY_TIME,
        consultation_service=get_consultation_service(),
        consultation_store=get_chat_history_repository(),
    )

    # 啟動雙階遞進用藥提醒排程引擎
    medication_scheduler = start_medication_scheduler(
        enabled=True,
        replier=get_line_replier(),
        user_profile_service=get_user_profile_service(),
    )

    try:
        # yield 期間代表 app 正在運行並處理 requests。
        yield
    finally:
        # shutdown 時取消背景排程 tasks
        if scheduler is not None:
            await scheduler.stop()
        if medication_scheduler is not None:
            await medication_scheduler.stop()



app = FastAPI(
    title="CARE Backend API",
    description="CARE 系統後端 API (包含 LINE Bot Webhook 與 LIFF REST API)",
    version="1.0.0",
    lifespan=lifespan,
)


# 藥袋影像上傳的大小限制必須在 ASGI 層攔截，路由層檢查太晚——見
# app/core/upload_limits.py 開頭的說明。放在 add_cors_middleware 之前，
# 讓 CORS 之後被加入而成為最外層，這樣即使這裡直接短路回應（不經過下游），
# 回應仍會被 CORS 包住、帶有正確的 CORS 標頭。
app.add_middleware(
    MaxUploadSizeMiddleware,
    path="/api/medications/prescription-scan",
    method="POST",
    max_bytes=settings.PRESCRIPTION_SCAN_MAX_IMAGE_BYTES,
)

# Centralized CORS config
add_cors_middleware(app)
app.include_router(system_router)
app.include_router(line_router, prefix="/line", tags=["LINE Bot"])
app.include_router(profile_router, prefix="/api/profiles", tags=["Profile"])
app.include_router(
    consultations_router, prefix="/api/consultations", tags=["Consultation"]
)
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(family_tree_router, prefix="/api/family", tags=["Family Tree"])
app.include_router(medications_router, prefix="/api/medications", tags=["Medications"])
# 前綴必須是 /api/medical：CARE-LIFF 的 medicalApi.ts 已經在打 /api/medical/nearby，
# 「附近醫院」整頁（路由、側邊欄、i18n）都已上線，缺的一直只有這一行掛載。
app.include_router(medical_router, prefix="/api/medical", tags=["Medical"])
app.include_router(
    knowledge_reports_router,
    prefix="/api/knowledge-reports",
    tags=["Knowledge Reports"],
)
app.include_router(
    admin_knowledge_reports_router,
    prefix="/api/admin/knowledge-reports",
    tags=["Knowledge Reports Admin"],
)
app.include_router(tts_router, prefix="/tts")
