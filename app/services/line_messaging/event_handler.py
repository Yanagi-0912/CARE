import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from linebot.v3.messaging import (
    ApiClient,
    AudioMessage,
    Configuration,
    LocationAction,
    Message,
    MessagingApi,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
    VideoMessageContent,
)

from app.core.config import settings
from app.services.history.history_service import LineMessageHistoryService
from app.services.media.mutimedia_processor import media_processor_service

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".svg",
}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}

DEFAULT_AUDIO_DURATION_MS = 60_000
HTTPS_SCHEME = "https://"
HTTP_SCHEME = "http" + "://"


class LineValidationError(Exception):
    """LINE message field validation failed."""


class LineEventHandler:
    def __init__(
        self,
        agent,
        channel_id: Optional[str],
        channel_secret: Optional[str],
        history_service: LineMessageHistoryService,
        user_profile_service=None,
        tts_service=None,
    ):
        self._agent = agent
        self._channel_id = channel_id
        self._channel_secret = channel_secret
        self._history_service = history_service
        self._user_profile_service = user_profile_service
        self._tts_service = tts_service
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_token(self) -> str:
        if self._access_token and self._token_expires_at:
            buffer_time = timedelta(minutes=5)
            if datetime.now(timezone.utc) < (self._token_expires_at - buffer_time):
                logger.debug("Using cached LINE access token")
                return self._access_token

        logger.info("LINE access token is missing or expired; fetching a new one.")
        if not self._channel_id or not self._channel_secret:
            raise ValueError(
                "LINE_CHANNEL_ID and LINE_CHANNEL_SECRET must be configured."
            )

        response = requests.post(
            "https://api.line.me/oauth2/v3/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self._channel_id,
                "client_secret": self._channel_secret,
            },
            timeout=10,
        )
        response.raise_for_status()

        result = response.json()
        access_token = result.get("access_token")
        expires_in = result.get("expires_in", 2592000)
        if not access_token:
            raise RuntimeError("LINE token response did not include access_token")

        self._access_token = access_token
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=expires_in
        )
        return access_token

    async def handle(self, event) -> None:
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        if not user_id or not reply_token:
            logger.warning("LINE event source missing user_id or reply_token")
            return

        if isinstance(event, PostbackEvent):
            await self._handle_postback_event(event, user_id, reply_token)
            return

        if not isinstance(event, MessageEvent):
            logger.warning("Unsupported event type: %s", type(event).__name__)
            return

        try:
            user_text, message_type = await self._extract_user_text(event, user_id)
            event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        except LineValidationError as exc:
            await self._send_reply(
                reply_token,
                str(exc),
                user_id,
                voice_reply_enabled=False,
            )
            return

        try:
            await self._invoke_agent_and_reply(
                user_text=user_text,
                message_type=message_type,
                event_time=event_time,
                reply_token=reply_token,
                user_id=user_id,
            )
        except Exception:
            logger.exception("Error in processing Line message event")
            await self._send_reply(
                reply_token,
                "抱歉，處理您的訊息時發生錯誤，請稍後再試",
                user_id,
                voice_reply_enabled=False,
            )

    async def _invoke_agent_and_reply(
        self,
        *,
        user_text: str,
        message_type: str,
        event_time: datetime,
        reply_token: str,
        user_id: str,
    ) -> None:
        logger.info("Processing message from user %s: %s...", user_id, user_text[:50])
        chat_history = await self._history_service.load_history(
            user_id=user_id,
            current_input=user_text,
            message_type=message_type,
        )
        agent_response = await self._agent.invoke(
            user_input=user_text,
            messages=chat_history,
        )
        response_text = (
            agent_response.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
        )
        success = await self._send_reply(
            reply_token,
            response_text,
            user_id,
            request_location=agent_response.get("call_request_location", False),
            voice_reply_enabled=await self._get_voice_reply_enabled(user_id),
        )

        if success:
            await self._history_service.save_turn(
                user_id=user_id,
                user_text=user_text,
                ai_reply=response_text,
                message_type=message_type,
                event_time=event_time,
            )
        logger.info("Successfully processed and replied to user %s", user_id)

    async def _send_reply(
        self,
        reply_token: str,
        message_text,
        user_id: str,
        request_location: bool = False,
        voice_reply_enabled: bool = True,
        tts_locale: str = "zh-TW",
    ) -> bool:
        try:
            access_token = self.get_token()
            normalized_text = self._normalize_message_text(message_text)
            messages = self._build_reply_messages(
                normalized_text,
                request_location=request_location,
            )
            self._append_audio_message(
                messages,
                normalized_text,
                voice_reply_enabled=voice_reply_enabled,
                tts_locale=tts_locale,
                user_id=user_id,
            )
            request = ReplyMessageRequest(replyToken=reply_token, messages=messages)
            await asyncio.to_thread(self._reply_message, access_token, request)
            logger.info("Message sent to LINE for user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to send LINE message")
            return False

    @staticmethod
    def _reply_message(access_token: str, request: ReplyMessageRequest) -> None:
        line_config = Configuration(access_token=access_token)
        with ApiClient(line_config) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(request)

    def _build_reply_messages(
        self,
        message_text: str,
        *,
        request_location: bool,
    ) -> list[Message]:
        return [
            TextMessage(
                text=message_text,
                quickReply=self._build_location_quick_reply()
                if request_location
                else None,
            )
        ]

    @staticmethod
    def _build_location_quick_reply() -> QuickReply:
        return QuickReply(
            items=[
                QuickReplyItem(
                    action=LocationAction(label="分享位置資訊"),
                )
            ]
        )

    @staticmethod
    def _normalize_message_text(message_text) -> str:
        if isinstance(message_text, str):
            return message_text
        if isinstance(message_text, list):
            return "".join(LineEventHandler._stringify_message_part(part) for part in message_text)
        if message_text is None:
            return ""
        return str(message_text)

    @staticmethod
    def _stringify_message_part(part) -> str:
        if isinstance(part, str):
            return part
        if isinstance(part, dict):
            return str(part.get("text", ""))
        return str(part)

    def _append_audio_message(
        self,
        messages: list[Message],
        message_text: str,
        *,
        voice_reply_enabled: bool,
        tts_locale: str,
        user_id: str,
    ) -> None:
        if not voice_reply_enabled:
            logger.info("Voice reply disabled for user %s; sending text only.", user_id)
            return
        if self._tts_service is None:
            return

        try:
            audio_message = self._create_audio_message(message_text, tts_locale)
        except Exception:
            logger.exception("TTS generation failed; falling back to text reply.")
            return

        if audio_message is not None:
            messages.append(audio_message)

    def _create_audio_message(
        self,
        message_text: str,
        tts_locale: str,
    ) -> Optional[AudioMessage]:
        if hasattr(self._tts_service, "available") and not self._tts_service.available():
            logger.warning("TTS service reports unavailable; attempting synthesis anyway.")

        _audio_bytes, output, duration_ms = self._tts_service.synthesize(
            message_text,
            locale=tts_locale,
        )
        audio_url = self._resolve_audio_url(output)
        if audio_url is None:
            return None

        return AudioMessage(
            original_content_url=audio_url,
            duration=int(duration_ms or DEFAULT_AUDIO_DURATION_MS),
        )

    def _resolve_audio_url(self, output: str) -> Optional[str]:
        if self._is_public_audio_url(output):
            return output

        tmp_path = Path(output)
        public_base_url = settings.PUBLIC_BASE_URL.rstrip("/")
        if not public_base_url:
            logger.warning("PUBLIC_BASE_URL is not set; skipping LINE audio reply.")
            return None
        if not tmp_path.exists():
            logger.warning("TTS output file not found: %s", tmp_path)
            return None

        audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
        return f"{public_base_url}/{audio_url_path}/{quote(tmp_path.name)}"

    @staticmethod
    def _is_public_audio_url(value: str) -> bool:
        return value.startswith((HTTPS_SCHEME, HTTP_SCHEME))

    async def _extract_user_text(self, event: MessageEvent, user_id: str):
        message = event.message

        if isinstance(message, TextMessageContent):
            return self._validate_text_message(message.text), "text"

        if isinstance(message, LocationMessageContent):
            return (
                f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}",
                "location",
            )

        if isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            return await self._extract_media_text(message, user_id)

        raise LineValidationError(f"不支援的訊息類型: {type(message).__name__}")

    async def _extract_media_text(self, message, user_id: str):
        media_id = message.id
        media_type = message.type
        file_name = getattr(message, "file_name", None)

        if media_type == "file" and file_name:
            media_type = self._infer_media_type_from_file_name(file_name)

        self._validate_media_message(media_id, media_type, file_name)
        media_content = await media_processor_service.process_media(
            media_message_id=media_id,
            user_media_type=media_type,
            source_file_name=file_name,
            user_id=user_id,
        )

        cleaned_content = media_content.strip()
        if (
            not cleaned_content
            or cleaned_content.startswith("Unable to extract text")
            or cleaned_content.startswith("發生錯誤")
        ):
            raise LineValidationError(
                f"無法從您傳送的{media_type}中辨識出任何文字，請確認內容清晰並重新傳送。"
            )

        return f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}", media_type

    @staticmethod
    def _validate_text_message(text: str) -> str:
        normalized_text = text.strip()
        if not normalized_text:
            raise LineValidationError("LINE 文字訊息不可為空白")
        return normalized_text

    @staticmethod
    def _validate_media_message(
        message_id: str,
        media_type: str,
        file_name: Optional[str] = None,
    ) -> None:
        if not message_id or not message_id.strip():
            raise LineValidationError("缺少 media message id")
        if media_type not in {"image", "video", "audio", "file"}:
            raise LineValidationError(f"不支援的媒體類型: {media_type}")
        if file_name is not None and not file_name.strip():
            raise LineValidationError("無效的媒體檔名")

    @staticmethod
    def _infer_media_type_from_file_name(file_name: str) -> str:
        extension = Path(file_name).suffix.lower()
        if extension in IMAGE_FILE_EXTENSIONS:
            return "image"
        if extension in VIDEO_FILE_EXTENSIONS:
            return "video"
        if extension in AUDIO_FILE_EXTENSIONS:
            return "audio"
        return "file"

    async def _get_voice_reply_enabled(self, user_id: str) -> bool:
        if self._user_profile_service is None:
            return True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile:
                return bool(profile.get("voice_reply_enabled", True))
        except Exception:
            logger.warning("Failed to fetch user voice preference", exc_info=True)
        return True

    async def _handle_postback_event(
        self,
        event: PostbackEvent,
        user_id: str,
        reply_token: str,
    ) -> None:
        params = self._parse_postback_data(event.postback.data)
        if params.get("action") != "toggle_voice_reply":
            await self._send_reply(
                reply_token,
                "抱歉，無法識別該操作。",
                user_id,
                voice_reply_enabled=False,
            )
            return

        enabled = params.get("enabled", "true").lower() == "true"
        response_text = await self._update_voice_reply_preference(user_id, enabled)
        await self._send_reply(
            reply_token,
            response_text,
            user_id,
            voice_reply_enabled=False,
        )

    @staticmethod
    def _parse_postback_data(postback_data: str) -> dict[str, str]:
        params = {}
        for param in postback_data.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key] = value
        return params

    async def _update_voice_reply_preference(self, user_id: str, enabled: bool) -> str:
        if self._user_profile_service is None:
            return "抱歉，語音回覆設定目前無法更新，請稍後再試。"

        profile = await self._user_profile_service.get_user_profile(user_id)
        if profile is None:
            return "抱歉，無法找到您的使用者資料。請稍後再試。"

        updated = await self._user_profile_service.update_voice_reply_enabled(
            user_id,
            enabled,
        )
        if not updated:
            return "抱歉，語音回覆設定更新失敗，請稍後再試。"

        status_text = "已開啟" if enabled else "已關閉"
        return f"語音回覆{status_text}成功"
