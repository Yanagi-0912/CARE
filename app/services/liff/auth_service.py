import asyncio
import logging

import requests
from fastapi import HTTPException

from app.core.config import settings
from app.services.liff.jwt_service import AppJwtService
from app.services.liff.line_id_token_service import LineIdTokenService
from app.services.liff.line_language_service import LineLanguageService
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "zh-TW"


class LiffAuthApplicationService:
    def __init__(
        self,
        line_id_token_service: LineIdTokenService,
        jwt_service: AppJwtService,
        user_profile_service: UserProfileService,
        line_language_service: LineLanguageService,
    ):
        self._line_id_token_service = line_id_token_service
        self._jwt_service = jwt_service
        self._user_profile_service = user_profile_service
        self._line_language_service = line_language_service

    async def login_with_id_token(self, id_token: str) -> dict:
        liff_client_id = settings.LIFF_CHANNEL_ID or settings.LINE_CHANNEL_ID

        if not liff_client_id:
            logger.error("LIFF_CHANNEL_ID/LINE_CHANNEL_ID 未設定，無法驗證 LIFF ID token")
            raise HTTPException(
                status_code=500,
                detail="LIFF_CHANNEL_ID (or LINE_CHANNEL_ID) is not configured",
            )

        try:
            # verify() 內部是同步的 requests，直接呼叫會鎖住整個事件迴圈——
            # 單執行緒的 FastAPI 在那段期間無法處理任何其他請求。移到工作執行緒
            # 執行；例外照樣會從執行緒往外拋，下面的分支不受影響。
            # 與 tts_service.py:110／:193 及 mutimedia_processor 採同一手法。
            verify_payload = await asyncio.to_thread(
                self._line_id_token_service.verify,
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

        display_name = verify_payload.get("name")
        picture_url = verify_payload.get("picture")

        profile = await self._user_profile_service.get_user_profile(line_user_id)
        if not profile:
            # 新使用者：向 LINE 查一次初始語言（查不到就用預設值），之後不再覆蓋。
            # 同樣是同步 requests，理由見上方 verify() 的說明。
            language = (
                await asyncio.to_thread(
                    self._line_language_service.get_language, line_user_id
                )
                or DEFAULT_LANGUAGE
            )
            await self._user_profile_service.create_default_user_profile(
                line_id=line_user_id,
                display_name=display_name,
                picture_url=picture_url,
                language=language,
            )
        else:
            # 舊使用者：一律以資料庫（settings.language）的值為準，
            # 不再打 Messaging API，避免蓋掉使用者在前端手動選擇的語言
            language = (profile.get("settings") or {}).get("language") or DEFAULT_LANGUAGE
            await self._user_profile_service.sync_line_profile(
                line_id=line_user_id,
                picture_url=picture_url,
            )

        access_token, expires_in = self._jwt_service.issue_for_user(line_user_id)
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "line_user_id": line_user_id,
            "language": language,
        }
