from datetime import datetime, timedelta, timezone
import logging

import jwt  # type: ignore[import-not-found]
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class LiffLoginRequest(BaseModel):
    id_token: str = Field(..., min_length=1)


class LiffLoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    line_user_id: str


@router.post("/liff/login", response_model=LiffLoginResponse)
async def liff_login(req: LiffLoginRequest):
    """
    前端送 LIFF ID token，後端向 LINE verify endpoint 驗證後，
    簽發應用內 JWT 給前端後續 API 使用。
    """
    if not settings.LINE_CHANNEL_ID:
        logger.error("LINE_CHANNEL_ID 未設定，無法驗證 LIFF ID token")
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ID is not configured")

    verify_url = "https://api.line.me/oauth2/v2.1/verify"

    try:
        verify_resp = requests.get(
            verify_url,
            params={
                "id_token": req.id_token,
                "client_id": settings.LINE_CHANNEL_ID,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.error(f"驗證 LIFF ID token 時 LINE API 連線失敗: {exc}")
        raise HTTPException(status_code=502, detail="Failed to verify id_token") from exc

    if verify_resp.status_code != 200:
        logger.warning(
            "LIFF ID token 驗證失敗，status=%s body=%s",
            verify_resp.status_code,
            verify_resp.text,
        )
        raise HTTPException(status_code=401, detail="Invalid LIFF id_token")

    verify_payload = verify_resp.json()
    line_user_id = verify_payload.get("sub")

    if not line_user_id:
        logger.error("LIFF ID token 驗證回應缺少 sub")
        raise HTTPException(status_code=401, detail="Invalid LIFF id_token payload")

    now = datetime.now(timezone.utc)
    expires_minutes = max(settings.AUTH_JWT_EXPIRES_MINUTES, 1)
    exp = now + timedelta(minutes=expires_minutes)

    app_token = jwt.encode(
        {
            "sub": line_user_id,
            "iss": "care-backend",
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        },
        settings.AUTH_JWT_SECRET,
        algorithm=settings.AUTH_JWT_ALGORITHM,
    )

    return LiffLoginResponse(
        access_token=app_token,
        expires_in=expires_minutes * 60,
        line_user_id=line_user_id,
    )
