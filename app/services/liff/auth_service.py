import logging

import requests
from fastapi import HTTPException

from app.core.config import settings
from app.services.liff.jwt_service import AppJwtService
from app.services.liff.line_id_token_service import LineIdTokenService
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)


class LiffAuthApplicationService:
    def __init__(
        self,
        line_id_token_service: LineIdTokenService,
        jwt_service: AppJwtService,
        user_profile_service: UserProfileService,
    ):
        self._line_id_token_service = line_id_token_service
        self._jwt_service = jwt_service
        self._user_profile_service = user_profile_service

    async def login_with_id_token(self, id_token: str) -> dict:
        liff_client_id = settings.LIFF_CHANNEL_ID or settings.LINE_CHANNEL_ID

        if not liff_client_id:
            logger.error("LIFF_CHANNEL_ID/LINE_CHANNEL_ID 未設定，無法驗證 LIFF ID token")
            raise HTTPException(
                status_code=500,
                detail="LIFF_CHANNEL_ID (or LINE_CHANNEL_ID) is not configured",
            )

        try:
            verify_payload = self._line_id_token_service.verify(
                id_token=id_token,
                client_id=liff_client_id,
            )
        except requests.RequestException as exc:
            logger.error(f"驗證 LIFF ID token 時 LINE API 連線失敗: {exc}")
            raise HTTPException(
                status_code=502, detail="Failed to verify id_token"
            ) from exc
        except ValueError as exc:
            logger.warning(f"LIFF ID token 驗證失敗: {exc}")
            raise HTTPException(
                status_code=401, detail="Invalid LIFF id_token"
            ) from exc

        line_user_id = verify_payload.get("sub")
        if not line_user_id:
            logger.error("LIFF ID token 驗證回應缺少 sub")
            raise HTTPException(status_code=401, detail="Invalid LIFF id_token payload")

        profile = await self._user_profile_service.get_user_profile(line_user_id)
        if not profile:
            await self._user_profile_service.create_default_user_profile(
                line_id=line_user_id,
                display_name=verify_payload.get("name"),
                picture_url=verify_payload.get("picture"),
            )

        access_token, expires_in = self._jwt_service.issue_for_user(line_user_id)
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "line_user_id": line_user_id,
        }
