"""LINE Messaging API Channel Access Token 管理。

負責向 LINE OAuth 換取 Bot 用的 access token，並做快取。
Webhook 回訊息、查 Profile language、Rich Menu 等都會共用這支。
"""

from __future__ import annotations

import asyncio
import logging
import threading
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
        # 用 threading.Lock 而不是 asyncio.Lock：刷新會同時被同步呼叫端
        # （mutimedia_processor 的 helper，跑在工作執行緒裡）與非同步呼叫端
        # （經 get_token_async → to_thread）觸發，兩者都在真實執行緒上，
        # asyncio.Lock 擋不住前者。
        self._refresh_lock = threading.Lock()

    def _cached_token(self) -> Optional[str]:
        """回傳仍在有效期內的快取 token，沒有則回 None。

        抽出來是為了讓同步與非同步兩條路徑共用同一份「還能不能用」的判定，
        避免兩邊各寫一次而在調整緩衝時間時失去同步。
        """
        if not (self._access_token and self._token_expires_at):
            return None
        # 提前 5 分鐘刷新，避免在使用時過期
        buffer_time = timedelta(minutes=5)
        if datetime.now(timezone.utc) < (self._token_expires_at - buffer_time):
            return self._access_token
        return None

    def get_token(self) -> str:
        """取得有效的 Channel Access Token；快取未過期則直接回傳。

        這是同步版本，會阻塞呼叫它的執行緒。從 `async def` 裡呼叫請改用
        `get_token_async()`，否則刷新期間整個事件迴圈會停住最多 10 秒。
        """
        cached = self._cached_token()
        if cached:
            logger.debug("使用緩存的 access token")
            return cached
        return self._refresh_token()

    async def get_token_async(self) -> str:
        """非同步取得 token：快取命中時直接回傳，只有刷新才切到工作執行緒。

        快取命中是絕大多數情況（token 有效期預設 30 天），此時不付任何執行緒
        調度成本。真正需要打 LINE OAuth 的時候才用 to_thread，避免那 10 秒的
        同步請求鎖住事件迴圈——注意 pod 啟動後的第一次呼叫必然會走到刷新，
        所以這不是罕見路徑，而是每次部署後的第一則訊息都會遇到。
        """
        cached = self._cached_token()
        if cached:
            logger.debug("使用緩存的 access token")
            return cached
        return await asyncio.to_thread(self._refresh_token)

    def _refresh_token(self) -> str:
        with self._refresh_lock:
            # double-check：等鎖期間可能已經有別的呼叫端刷新完成，
            # 沒有這一步的話，並行的多個呼叫會各打一次 LINE OAuth。
            cached = self._cached_token()
            if cached:
                return cached
            return self._request_new_token()

    def _request_new_token(self) -> str:
        logger.debug("緩存的 token 已過期或不存在，正在獲取新的 token...")
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
            expires_in = result.get("expires_in", 2592000)  # 預設 30 天 (秒)

            if not access_token:
                raise RuntimeError("API 返回的響應中沒有 access_token")

            self._access_token = access_token
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )

            logger.debug(
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
