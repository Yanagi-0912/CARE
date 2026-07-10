import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

from app.routers.users.family_tree import router as family_tree_router

logging.basicConfig(level=logging.INFO)

AUDIO_NOT_FOUND_DETAIL = "Audio not found"
TTS_NOT_FOUND_RESPONSE = {
    404: {
        "description": AUDIO_NOT_FOUND_DETAIL,
        "content": {
            "application/json": {
                "example": {"detail": AUDIO_NOT_FOUND_DETAIL},
            }
        },
    }
}


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

tts_tmp_dir = Path("app_data") / "tmp"
tts_tmp_dir.mkdir(parents=True, exist_ok=True)


@app.get(
    "/tts/{filename}",
    include_in_schema=False,
    responses=TTS_NOT_FOUND_RESPONSE,
)
async def get_tts_audio(filename: str):
    if not filename.startswith("tts_") or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)
    if Path(filename).suffix.lower() != ".mp3":
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)

    audio_path = tts_tmp_dir / filename
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)

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
