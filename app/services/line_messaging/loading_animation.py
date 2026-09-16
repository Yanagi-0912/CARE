"""LINE Loading Animation API 封裝。"""

from __future__ import annotations

import asyncio
import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ShowLoadingAnimationRequest,
)

from app.core.config import settings
from app.services.line_messaging.token_manager import LineTokenManager

logger = logging.getLogger(__name__)

# LINE API：5–60 秒（須為 5 的倍數）；RAG／web fallback 常超過 10s
DEFAULT_LOADING_SECONDS = 60


class LineLoadingAnimationService:
    """對一對一聊天顯示 LINE Loading Animation。"""

    def __init__(self, token_manager: LineTokenManager) -> None:
        self._token_manager = token_manager

    async def start(
        self,
        chat_id: str,
        loading_seconds: int = DEFAULT_LOADING_SECONDS,
    ) -> None:
        if not chat_id or not chat_id.strip():
            return

        try:
            access_token = await self._token_manager.get_token_async()

            # 同步 SDK 直接在事件迴圈裡呼叫會把整個迴圈凍住（理由同
            # reply.py 的 _call_line_api）：動畫只是附加效果，卻能在 LINE 端
            # 停滯時拖住所有使用者的 webhook。丟到執行緒並帶逾時。
            def _do_call() -> None:
                line_config = Configuration(access_token=access_token)
                with ApiClient(line_config) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.show_loading_animation(
                        ShowLoadingAnimationRequest(
                            chat_id=chat_id,
                            loading_seconds=loading_seconds,
                        ),
                        _request_timeout=settings.LINE_API_TIMEOUT_SECONDS,
                    )

            await asyncio.to_thread(_do_call)
            logger.debug(
                "LINE loading animation started for chat %s (%ss)",
                chat_id,
                loading_seconds,
            )
        except Exception as ex:
            logger.error(
                "Failed to show LINE loading animation for chat %s: %s",
                chat_id,
                ex,
                exc_info=True,
            )
