"""CARE 官方帳號的 basic ID（例如 @460xmyhp）；分享卡的加好友連結與 QR 都靠它。

不放進設定檔：向 LINE 問（Messaging API 的 Get bot info）就拿得到，而且問到的是
目前這組 channel token 所屬的帳號——測試環境換成別的官方帳號時會自動對上，不必
多維護一個可能跟 token 對不上的設定值。backend 的 ConfigMap 也只產生固定的鍵，
新增設定還得連 CARE-infra 的模板一起改。

只記住成功的結果：LINE 暫時出錯時回 None、下一次再問，不會把一次失敗記上整個
程序的生命週期。
"""

import asyncio
import logging
from typing import Optional

from linebot.v3.messaging import ApiClient, Configuration, MessagingApi

logger = logging.getLogger(__name__)


class OfficialAccountService:
    def __init__(self, token_manager) -> None:
        self._token_manager = token_manager
        self._basic_id: Optional[str] = None

    async def get_basic_id(self) -> Optional[str]:
        """帶 @ 的 basic ID；問不到時回 None。"""
        if self._basic_id:
            return self._basic_id
        try:
            token = await self._token_manager.get_token_async()
            # SDK 的 MessagingApi 是同步 requests，丟到 thread 避免卡住 event loop。
            info = await asyncio.to_thread(self._fetch_bot_info, token)
        except Exception:
            logger.exception("取得官方帳號 basic ID 失敗")
            return None

        basic_id = (getattr(info, "basic_id", "") or "").strip()
        if not basic_id:
            logger.error("LINE 回傳的 bot info 沒有 basic ID")
            return None
        self._basic_id = basic_id
        return basic_id

    @staticmethod
    def _fetch_bot_info(access_token: str):
        with ApiClient(Configuration(access_token=access_token)) as api_client:
            return MessagingApi(api_client).get_bot_info()
