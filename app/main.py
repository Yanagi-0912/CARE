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
from app.dependencies import (
    get_consultation_service,
    get_chat_history_repository,
    get_line_replier,
    get_user_profile_service,
    start_medication_scheduler,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.services.consultation.scheduler import (
    start_consultation_daily_summary_scheduler,
)
from app.services.rag.user_document_store import ensure_user_docs_indexes_on_startup

from app.routers.users.family_tree import router as family_tree_router
from app.routers.users.knowledge_reports import router as knowledge_reports_router
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
