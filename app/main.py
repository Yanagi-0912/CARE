import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routers.line.webhook import router as line_router
from app.routers.liff.auth import router as auth_router
from app.routers.system import router as system_router
from app.routers.users.upsert_users import router as profile_router
from app.routers.users.consultations import router as consultations_router
from app.core.cors import add_cors_middleware
from app.core.config import settings
from app.dependencies import get_consultation_service, get_chat_history_repository
from app.repositories.consultation_repository import ConsultationRepository
from app.services.consultation.scheduler import (
    start_consultation_daily_summary_scheduler,
)

from app.routers.family_tree import router as family_tree_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI lifespan 會在 yield 前執行 startup 邏輯。
    # 先確認 MongoDB 摘要 collection 有 TTL index，讓摘要只保留 7 天。
    await ConsultationRepository.ensure_indexes()

    # 這裡啟動每日諮詢摘要排程，讓它在背景 task 中持續等待下一次執行時間。
    scheduler = start_consultation_daily_summary_scheduler(
        enabled=True,  # 啟動自動排程
        run_time=settings.CONSULTATION_DAILY_SUMMARY_TIME,
        consultation_service=get_consultation_service(),
        consultation_store=get_chat_history_repository(),
    )
    try:
        # yield 期間代表 app 正在運行並處理 requests。
        # 當 app 關閉時，FastAPI 會離開這個 yield，繼續執行 finally 清理邏輯。
        yield
    finally:
        # shutdown 時取消背景排程 task
        if scheduler is not None:
            await scheduler.stop()


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
