"""LINE Loading Animation API 封裝。"""

from __future__ import annotations

import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ShowLoadingAnimationRequest,
)

from app.services.line_messaging.token_manager import LineTokenManager

logger = logging.getLogger(__name__)

DEFAULT_LOADING_SECONDS = 10


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
            access_token = self._token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.show_loading_animation(
                    ShowLoadingAnimationRequest(
                        chat_id=chat_id,
                        loading_seconds=loading_seconds,
                    )
                )
            logger.info(
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
