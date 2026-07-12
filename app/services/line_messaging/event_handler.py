import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from linebot.v3.messaging import (
    ApiClient,
    AudioMessage,
    Configuration,
    LocationAction,
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
    TextMessageContent,
    VideoMessageContent,
)

from app.core.config import settings
from app.services.history.history_service import LineMessageHistoryService
from app.services.line_messaging.token_manager import LineTokenManager
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


class LineValidationError(Exception):
    """LINE 訊息欄位格式或內容驗證失敗。"""


class LineEventHandler:
    def __init__(
        self,
        agent,
        token_manager: LineTokenManager,
        history_service: LineMessageHistoryService,
        user_profile_service=None,
        tts_service=None,
    ):
        self._agent = agent
        self._token_manager = token_manager
        self._history_service = history_service
        self._user_profile_service = user_profile_service
        self._tts_service = tts_service

    async def handle(self, event: MessageEvent) -> None:
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        if not user_id or not reply_token:
            logger.warning("LINE event source missing user_id or reply_token")
            return

        async def send_reply(
            reply_token_str: str,
            message_text: str,
            uid: str,
            request_location: bool = False,
            voice_reply_enabled: bool = True,
        ) -> bool:
            try:
                if not reply_token_str or not reply_token_str.strip():
                    raise ValueError("LINE 事件缺少 reply_token")
                if not uid or not uid.strip():
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
                            replyToken=reply_token_str,
                            messages=messages,
                        )
                    )

                logger.info("Message sent to LINE for user %s", uid)
                return True

            except Exception:
                logger.exception("Failed to send LINE message")
                return False

        try:
            message = event.message
            user_text = ""
            message_type = ""

            if isinstance(message, TextMessageContent):
                user_text, message_type = message.text, "text"

            elif isinstance(message, LocationMessageContent):
                user_text = (
                    f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}"
                )
                message_type = "location"

            elif isinstance(
                message,
                (
                    ImageMessageContent,
                    VideoMessageContent,
                    AudioMessageContent,
                    FileMessageContent,
                ),
            ):
                user_text, message_type = await self._extract_media_text(message, user_id)

            else:
                logger.warning("Unsupported message type: %s", type(message).__name__)
                return

            event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        except LineValidationError as e:
            await send_reply(
                reply_token,
                str(e),
                user_id,
                voice_reply_enabled=False,
            )
            return

        try:
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
            call_request_location = agent_response.get("call_request_location", False)
            voice_reply_enabled = await self._get_voice_reply_enabled(user_id)

            success = await send_reply(
                reply_token,
                response_text,
                user_id,
                request_location=call_request_location,
                voice_reply_enabled=voice_reply_enabled,
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

        except Exception:
            logger.exception("Error in processing Line message event")
            await send_reply(
                reply_token,
                "抱歉，處理您的訊息時發生錯誤，請稍後再試",
                user_id,
                voice_reply_enabled=False,
            )

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

    async def _extract_media_text(self, message, user_id: str) -> tuple[str, str]:
        media_id = message.id
        media_type = message.type
        file_name = getattr(message, "file_name", None)

        if media_type == "file" and file_name:
            extension = Path(file_name).suffix.lower()
            if extension in IMAGE_FILE_EXTENSIONS:
                media_type = "image"
            elif extension in VIDEO_FILE_EXTENSIONS:
                media_type = "video"
            elif extension in AUDIO_FILE_EXTENSIONS:
                media_type = "audio"
            else:
                media_type = "file"

        if not media_id or not media_id.strip():
            raise LineValidationError("缺少 media message id")
        if media_type not in {"image", "video", "audio", "file"}:
            raise LineValidationError(f"不支援的媒體類型: {media_type}")
        if file_name is not None and not file_name.strip():
            raise LineValidationError("無效的媒體檔名")

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

    async def _get_voice_reply_enabled(self, user_id: str) -> bool:
        if self._user_profile_service is None:
            return True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.warning("Failed to fetch user voice preference", exc_info=True)
            return True
        if not profile:
            return True
        return bool(profile.get("voice_reply_enabled", True))

    def _append_tts_audio_message(
        self,
        messages,
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
