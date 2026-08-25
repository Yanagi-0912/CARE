from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated
import jwt  # type: ignore[import-not-found]
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from pydantic import BaseModel, Field
from app.dependencies import (
    CurrentUser,
    get_consultation_download_token_service,
    get_consultation_service,
    get_current_user,
    get_family_authorization_service,
)
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationViewResponse,
    ConsultationSummary,
)
from app.services.consultation.consultation_service import ConsultationService
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.gemini.shared.errors import GeminiHttpError
from app.services.liff.jwt_service import AppJwtService

router = APIRouter(tags=["Consultation"])

DB_ERROR_DETAIL = "資料庫連線異常，請稍後再試"


class DownloadTokenResponse(BaseModel):
    downloadToken: str = Field(..., description="下載用短效 token")
    expiresIn: int = Field(..., description="token 有效秒數")


def _build_download_response(payload: list[dict]) -> Response:
    filename = f"CARE_consult_summart_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/me/summary/latest",
    response_model=ConsultationViewResponse,
    summary="取得目前使用者諮詢紀錄",
    description="優先回傳最新的摘要，如果沒有摘要會回傳None",
)
async def get_my_consultations(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
) -> ConsultationViewResponse:
    try:
        summary = await consultation_service.get_view(current_user.line_user_id)
        return ConsultationViewResponse(
            line_id=current_user.line_user_id,
            view_type="summary",
            summary=summary.summary if summary else None,
            language=summary.language if summary else None,
        )
    except (RedisError, PyMongoError):
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)


@router.get(
    "/me/messages/raw",
    response_model=ConsultationViewResponse,
    summary="取得原始諮詢快取",
    description="直接回傳 Redis 內的原始對話。",
)
async def get_raw_consultations(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
) -> ConsultationViewResponse:
    try:
        messages = await consultation_service.get_raw_view(current_user.line_user_id)
        return ConsultationViewResponse(
            line_id=current_user.line_user_id,
            view_type="raw",
            messages=messages,
        )
    except RedisError:
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)


@router.get(
    "/me/allsummaries",
    response_model=list[ConsultationSummary],
    summary="取得目前使用者所有摘要紀錄",
    description="直接回傳目前登入使用者在 MongoDB 中的所有諮詢摘要，依日期由新到舊排序。",
)
async def get_my_summary_history(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
) -> list[ConsultationSummary]:
    try:
        return await consultation_service.get_all_summaries(current_user.line_user_id)
    except PyMongoError:
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)


@router.get(
    "/me/summary/downloadtoken",
    response_model=DownloadTokenResponse,
    summary="取得摘要下載 token",
    description="先由 LIFF 前端帶著登入態呼叫，取得短效 downloadToken。",
)
async def get_my_summary_download_token(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    download_token_service: Annotated[
        AppJwtService, Depends(get_consultation_download_token_service)
    ],
) -> DownloadTokenResponse:
    download_token, expires_in = download_token_service.issue_for_user(
        current_user.line_user_id
    )
    return DownloadTokenResponse(
        downloadToken=download_token,
        expiresIn=expires_in,
    )


@router.get(
    "/me/summary/download",
    summary="下載目前使用者所有摘要紀錄",
    description="以 JSON 檔案下載目前登入使用者的所有諮詢摘要紀錄。",
)
async def download_my_summary_history(
    download_token: Annotated[str, Query(alias="downloadToken", min_length=1)] = ...,
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ] = ...,
    download_token_service: Annotated[
        AppJwtService, Depends(get_consultation_download_token_service)
    ] = ...,
) -> ConsultationSummary:
    try:
        current_user_id = download_token_service.decode_user_id(download_token)
        summaries = await consultation_service.get_all_summaries(current_user_id)
        payload = [
            summary.model_dump(mode="json", exclude_none=True) for summary in summaries
        ]
        return _build_download_response(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="downloadToken expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid downloadToken")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid downloadToken")
    except PyMongoError:
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)


@router.post(
    "/me/summary/generate",
    response_model=ConsultationSummary,
    summary="手動摘要諮詢紀錄",
    description="把指定日期或今天的對話摘要後寫入 MongoDB。",
)
async def summarize_consultations(
    request: ConsultationSummarizeRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
) -> ConsultationSummary:
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


@router.get(
    "/{userId}/allsummaries",
    response_model=list[ConsultationSummary],
    summary="取得指定家庭成員的所有摘要紀錄",
    description=(
        "供 LIFF 家庭頁查看家人的諮詢摘要，回傳格式與 /me/allsummaries 相同。"
        "出於安全考量，請求者與目標使用者必須在同一個家庭族譜內。"
    ),
)
async def get_member_summary_history(
    userId: str,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
    authz: Annotated[
        FamilyAuthorizationService, Depends(get_family_authorization_service)
    ],
) -> list[ConsultationSummary]:
    # PRIVATE 讀取權在**讀取資料之前**判定。對話摘要是三級裡最敏感的，
    # 「先撈出來再過濾」會讓它在無權的請求脈絡中被實體化——一次記錄外洩或
    # 例外堆疊就足以把它帶出去。
    await authz.authorize(current_user.line_user_id, userId, "PRIVATE", "READ")
    try:
        return await consultation_service.get_all_summaries(userId)
    except PyMongoError:
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)


@router.get(
    "/{userId}/messages/raw",
    response_model=ConsultationViewResponse,
    summary="取得指定家庭成員的原始諮詢快取",
    description=(
        "供 LIFF 家庭頁查看家人的原始對話，回傳格式與 /me/messages/raw 相同。"
        "出於安全考量，請求者與目標使用者必須在同一個家庭族譜內。"
    ),
)
async def get_member_raw_consultations(
    userId: str,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    consultation_service: Annotated[
        ConsultationService, Depends(get_consultation_service)
    ],
    authz: Annotated[
        FamilyAuthorizationService, Depends(get_family_authorization_service)
    ],
) -> ConsultationViewResponse:
    # 原始逐句對話存於 Redis，授權必須先於讀取（同上）。
    await authz.authorize(current_user.line_user_id, userId, "PRIVATE", "READ")
    try:
        messages = await consultation_service.get_raw_view(userId)
        return ConsultationViewResponse(
            line_id=userId,
            view_type="raw",
            messages=messages,
        )
    except RedisError:
        raise HTTPException(status_code=503, detail=DB_ERROR_DETAIL)