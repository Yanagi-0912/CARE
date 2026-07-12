"""LINE Messaging API Channel Access Token 管理。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class LineTokenManager:
    """管理 LINE Channel Access Token 的取得與快取。"""

    def __init__(
        self,
        channel_id: Optional[str],
        channel_secret: Optional[str],
    ) -> None:
        self._channel_id = channel_id
        self._channel_secret = channel_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_token(self) -> str:
        """取得有效的 Channel Access Token；快取未過期則直接回傳。"""
        if self._access_token and self._token_expires_at:
            buffer_time = timedelta(minutes=5)
            if datetime.now(timezone.utc) < (self._token_expires_at - buffer_time):
                logger.debug("使用緩存的 access token")
                return self._access_token

        logger.info("緩存的 token 已過期或不存在，正在獲取新的 token...")
        if not self._channel_id or not self._channel_secret:
            raise ValueError(
                "無法獲取 token：LINE_CHANNEL_ID 和 LINE_CHANNEL_SECRET 未設定。"
                "請在 .env 檔案中設定這些變數。"
            )

        url = "https://api.line.me/oauth2/v3/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self._channel_id,
            "client_secret": self._channel_secret,
        }

        try:
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()

            result = response.json()
            access_token = result.get("access_token")
            expires_in = result.get("expires_in", 2592000)

            if not access_token:
                raise RuntimeError("API 返回的響應中沒有 access_token")

            self._access_token = access_token
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )

            logger.info(
                "成功獲取新的 access token，有效期至: %s",
                self._token_expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
            return access_token

        except requests.exceptions.RequestException as e:
            error_msg = f"獲取 access token 失敗: {e}"
            if hasattr(e, "response") and e.response is not None:
                error_msg += f"\nAPI 響應: {e.response.text}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
