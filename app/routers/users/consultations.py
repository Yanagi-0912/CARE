from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app.dependencies import CurrentUser, get_consultation_service, get_current_user
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationViewResponse,
    ConsultationSummary,
)
from app.services.consultation.consultation_service import ConsultationService
from app.infrastructure.gemini.shared.errors import GeminiHttpError

router = APIRouter(tags=["Consultation"])


@router.get(
    "/me",
    response_model=ConsultationViewResponse,
    summary="取得目前使用者諮詢紀錄",
    description="優先回傳摘要，若今天尚未產生摘要則回傳原始對話。",
)
async def get_my_consultations(
    current_user: CurrentUser = Depends(get_current_user),
    consultation_service: ConsultationService = Depends(get_consultation_service),
):
    try:
        return await consultation_service.get_view(current_user.line_user_id)
    except (RedisError, PyMongoError):
        raise HTTPException(status_code=503, detail="資料庫連線異常，請稍後再試")


@router.get(
    "/me/today",
    response_model=ConsultationViewResponse,
    summary="取得今天的諮詢紀錄",
    description="回傳今天的摘要或原始對話。",
)
async def get_today_consultations(
    current_user: CurrentUser = Depends(get_current_user),
    consultation_service: ConsultationService = Depends(get_consultation_service),
):
    try:
        return await consultation_service.get_view(current_user.line_user_id)
    except (RedisError, PyMongoError):
        raise HTTPException(status_code=503, detail="資料庫連線異常，請稍後再試")


@router.get(
    "/me/raw",
    response_model=ConsultationViewResponse,
    summary="取得原始諮詢快取",
    description="直接回傳 Redis 內的原始對話。",
)
async def get_raw_consultations(
    current_user: CurrentUser = Depends(get_current_user),
    consultation_service: ConsultationService = Depends(get_consultation_service),
):
    try:
        return await consultation_service.get_raw_view(current_user.line_user_id)
    except RedisError:
        raise HTTPException(status_code=503, detail="Redis 連線異常，請稍後再試")


@router.post(
    "/me/summarize",
    response_model=ConsultationSummary,
    summary="手動摘要諮詢紀錄",
    description="把指定日期或今天的對話摘要後寫入 MongoDB。",
)
async def summarize_consultations(
    request: ConsultationSummarizeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    consultation_service: ConsultationService = Depends(get_consultation_service),
):
    try:
        return await consultation_service.summarize(current_user.line_user_id, request)
    except GeminiHttpError as exc:
        if exc.status_code == 429:
            raise HTTPException(status_code=429, detail="AI 額度已達上限，請稍後再試")
        raise HTTPException(status_code=502, detail=str(exc))
    except RedisError:
        raise HTTPException(status_code=503, detail="Redis 連線異常，請稍後再試")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB 連線異常，請稍後再試")
