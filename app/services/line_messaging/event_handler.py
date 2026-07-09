import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

from app.services.history.history_service import LineMessageHistoryService
from app.services.line_messaging.shared.errors import LineValidationError
from app.services.line_messaging.shared.validation import (
    validate_media_message,
    validate_reply_context,
    validate_text_message,
)
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


class LineEventHandler:
    def __init__(
        self,
        agent,
        line_message_service,
        user_profile_service,
        history_service: LineMessageHistoryService,
    ):
        self._agent = agent
        self._line_message_service = line_message_service
        self._user_profile_service = user_profile_service
        self._history_service = history_service

    async def handle(self, event) -> None:
        user_id = getattr(event.source, "user_id", None)

        if isinstance(event, PostbackEvent):
            await self._handle_postback_event(event, user_id)
            return

        if not isinstance(event, MessageEvent):
            logger.warning("Unsupported event type: %s", type(event).__name__)
            return

        reply_token = event.reply_token
        try:
            validate_reply_context(reply_token, user_id)
            user_text, message_type = await self._extract_user_text(event, user_id)
            event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        except LineValidationError as exc:
            await self._line_message_service.send_line_reply(
                reply_token,
                str(exc),
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
                agent_response.get("response")
                or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = agent_response.get("call_request_location", False)
            voice_reply_enabled = await self._get_voice_reply_enabled(user_id)

            success = await self._line_message_service.send_line_reply(
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
            await self._line_message_service.send_line_reply(
                reply_token,
                "抱歉，處理您的訊息時發生錯誤，請稍後再試",
                user_id,
                voice_reply_enabled=False,
            )

    async def _extract_user_text(self, event: MessageEvent, user_id: Optional[str]):
        message = event.message

        if isinstance(message, TextMessageContent):
            return validate_text_message(message.text), "text"

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
            media_id = message.id
            media_type = message.type
            file_name = getattr(message, "file_name", None)

            if media_type == "file" and file_name:
                media_type = self._infer_media_type_from_file_name(file_name)

            validate_media_message(media_id, media_type, file_name)

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
                    f"無法從您傳送的{media_type}中辨識出任何文字，"
                    "請確認內容清晰並重新傳送。"
                )

            return f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}", media_type

        raise LineValidationError(f"不支援的訊息類型: {type(message).__name__}")

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

    async def _get_voice_reply_enabled(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile:
                return bool(profile.get("voice_reply_enabled", True))
        except Exception:
            logger.warning("Failed to fetch user voice preference", exc_info=True)
        return True

    async def _handle_postback_event(
        self, event: PostbackEvent, user_id: Optional[str]
    ) -> None:
        reply_token = event.reply_token
        try:
            validate_reply_context(reply_token, user_id)
            postback_data = event.postback.data
            logger.info("Received postback from user %s: %s", user_id, postback_data)

            params = {}
            for param in postback_data.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    params[key] = value

            if params.get("action") != "toggle_voice_reply":
                await self._line_message_service.send_line_reply(
                    reply_token,
                    "抱歉，無法識別該操作。",
                    user_id,
                    voice_reply_enabled=False,
                )
                return

            enabled = params.get("enabled", "true").lower() == "true"
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile is None:
                response_text = "抱歉，無法找到您的使用者資料。請稍後再試。"
            else:
                updated = await self._user_profile_service.update_voice_reply_enabled(
                    user_id,
                    enabled,
                )
                if updated:
                    status_text = "已開啟" if enabled else "已關閉"
                    response_text = f"語音回覆{status_text}成功"
                else:
                    response_text = "抱歉，語音回覆設定更新失敗，請稍後再試。"

            await self._line_message_service.send_line_reply(
                reply_token,
                response_text,
                user_id,
                voice_reply_enabled=False,
            )

        except Exception:
            logger.exception("Error handling postback event")
            if reply_token:
                await self._line_message_service.send_line_reply(
                    reply_token,
                    "處理您的操作時發生錯誤，請稍後再試。",
                    user_id,
                    voice_reply_enabled=False,
                )
