"""LINE Messaging API Channel Access Token 與回覆管理。

負責向 LINE OAuth 換取 Bot 用的 access token 並作快取，以及建構回覆訊息（包含 Text、Audio/TTS）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
import requests

from linebot.v3.messaging import (
    ApiClient,
    AudioMessage,
    Configuration,
    FlexMessage,
    LocationAction,
    MessagingApi,
    PushMessageRequest,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
)

from app.core.config import settings
from app.services.line_messaging.token_manager import LineTokenManager

logger = logging.getLogger(__name__)

DEFAULT_AUDIO_DURATION_MS = 60_000


class LineReplier:
    """負責組織回覆內容並呼叫 LINE SDK 送出。"""

    def __init__(self, token_manager: LineTokenManager, tts_service=None) -> None:
        self._token_manager = token_manager
        self._tts_service = tts_service

    async def reply(
        self,
        reply_token: str,
        message_text: str,
        user_id: str,
        request_location: bool = False,
        voice_reply_enabled: bool = True,
    ) -> bool:
        """發送 LINE 訊息（包含文字訊息與選填的 TTS 語音訊息）"""
        try:
            if not reply_token or not reply_token.strip():
                raise ValueError("LINE 事件缺少 reply_token")
            if not user_id or not user_id.strip():
                raise ValueError("LINE 事件缺少 user_id")

            access_token = self._token_manager.get_token()
            message_text = self._normalize_message_text(message_text)
            text_message = self._build_text_message(message_text, request_location)
            messages = [text_message]
            self._append_tts_audio_message(
                messages,
                message_text,
                voice_reply_enabled=voice_reply_enabled,
            )

            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=messages,
                    )
                )

            logger.info("Message sent to LINE for user %s", user_id)
            return True

        except Exception:
            logger.exception("Failed to send LINE message")
            return False

    async def reply_flex(
        self, reply_token: str, flex_message: FlexMessage, user_id: str
    ) -> bool:
        """回覆 LINE Flex Message"""
        try:
            if not reply_token or not reply_token.strip():
                raise ValueError("LINE 事件缺少 reply_token")

            access_token = self._token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[flex_message],
                    )
                )
            logger.info("Flex Message replied to LINE user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to reply LINE Flex message")
            return False

    async def push_flex(self, user_id: str, flex_message: FlexMessage) -> bool:
        """主動推播 LINE Flex Message"""
        try:
            if not user_id or not user_id.strip():
                raise ValueError("LINE 推播缺少 user_id")

            access_token = self._token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=[flex_message],
                    )
                )
            logger.info("Flex Message pushed to LINE user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to push LINE Flex message")
            return False


    @staticmethod
    def _normalize_message_text(message_text: Any) -> str:
        if isinstance(message_text, str):
            return message_text
        if isinstance(message_text, list):
            return "".join(
                part
                if isinstance(part, str)
                else (part.get("text", "") if isinstance(part, dict) else str(part))
                for part in message_text
            )
        if message_text is None:
            return ""
        return str(message_text)

    @staticmethod
    def _build_text_message(message_text: str, request_location: bool) -> TextMessage:
        if request_location:
            quick_reply = QuickReply(
                items=[
                    QuickReplyItem(action=LocationAction(label="分享位置資訊")),
                ]
            )
            return TextMessage(text=message_text, quickReply=quick_reply)
        return TextMessage(text=message_text)

    def _append_tts_audio_message(
        self,
        messages: list,
        message_text: str,
        *,
        voice_reply_enabled: bool,
    ) -> None:
        if not voice_reply_enabled or self._tts_service is None:
            return

        try:
            _audio_bytes, output, duration_ms = self._tts_service.synthesize(
                message_text,
                locale="zh-TW",
            )
            audio_url = self._resolve_audio_url(output)
        except Exception:
            logger.exception("TTS generation failed; falling back to text reply.")
            return

        if audio_url:
            messages.append(
                AudioMessage(
                    original_content_url=audio_url,
                    duration=int(duration_ms or DEFAULT_AUDIO_DURATION_MS),
                )
            )

    @staticmethod
    def _resolve_audio_url(output: str) -> Optional[str]:
        if output.startswith(("https://", "http" + "://")):
            return output

        audio_path = Path(output)
        if not settings.PUBLIC_BASE_URL.strip():
            logger.warning("PUBLIC_BASE_URL is not set; skipping LINE audio reply.")
            return None
        if not audio_path.exists():
            logger.warning("TTS output file not found: %s", audio_path)
            return None

        audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
        return (
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
            f"{audio_url_path}/{quote(audio_path.name)}"
        )
