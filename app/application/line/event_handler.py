"""
Line Event Handler
處理 webhook 接受到的 event 並分發到對應的 service
目前處理三種事件:
1.文字訊息
2.位置訊息
3.多媒體訊息
"""

from pathlib import Path
from typing import Optional, cast
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from app.application.medical.medical_service import (
    format_facility_list,
    NO_FACILITY_MESSAGE,
)
from app.application.media.mutimedia_processor import media_processor_service
from app.infrastructure.line.shared.validation import (
    validate_media_message,
    validate_reply_context,
    validate_text_message,
)
import logging

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
        response_orchestrator,
        line_message_service,
        medical_service,
    ):
        self._response_orchestrator = response_orchestrator
        self._line_message_service = line_message_service
        self._medical_service = medical_service

    async def handle(self, event: MessageEvent) -> None:
        user_id = getattr(event.source, "user_id", None)
        validate_reply_context(event.reply_token, user_id)

        reply_token = event.reply_token
        message = event.message

        if isinstance(message, TextMessageContent):
            await self._handle_text_message(message, reply_token, user_id)
        elif isinstance(message, LocationMessageContent):
            await self._handle_location_message(message, reply_token, user_id)
        elif isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            await self._handle_media_message(message, reply_token, user_id)
        else:
            logger.warning(f"Unsupported message type: {type(message).__name__}")

    async def _handle_text_message(
        self, message: TextMessageContent, reply_token: str, user_id: Optional[str]
    ) -> None:
        logger.info(f"Received text message event from user {user_id}")
        user_text = validate_text_message(message.text)

        await self._process_and_reply_flow(
            user_text=user_text,
            reply_token=reply_token,
            user_id=user_id,
        )
    async def _handle_location_message(
        self, message: LocationMessageContent, reply_token: str, user_id: Optional[str]
    ) -> None:
        lat: float = message.latitude
        lng: float = message.longitude

        logger.info(f"Received location from user {user_id}: ({lat}, {lng})")

        facilities = await self._medical_service.handle_location(user_id, lat, lng)
        if facilities is None:
            return

        reply_text = (
            format_facility_list(facilities) if facilities else NO_FACILITY_MESSAGE
        )
        await self._line_message_service.send_line_reply(
            reply_token, reply_text, user_id
        )

    async def _handle_media_message(
        self, message, reply_token: str, user_id: Optional[str]
    ) -> None:
        media_id = message.id
        media_type = message.type
        file_name = getattr(message, "file_name", None)

        if media_type == "file" and file_name:
            media_type = self._infer_media_type_from_file_name(file_name)
        validate_media_message(media_id, media_type, file_name)

        log_msg = f"Received {media_type} message event from user {user_id}"
        if file_name:
            log_msg += f": {file_name}"
        logger.info(log_msg)

        media_content = await media_processor_service.process_media(
            media_message_id=media_id,
            user_media_type=media_type,
            source_file_name=file_name,
            user_id=user_id,
        )

        await self._process_and_reply_flow(
            user_text=f"以下為用戶傳送的{media_type}媒體內容：\n{media_content}",
            reply_token=reply_token,
            user_id=user_id,
        )

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

    async def _process_and_reply_flow(
        self, user_text: str, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")
            result = await self._response_orchestrator.orchestrate_response(user_text)

            if result.is_function_call and result.function_name == "request_location":
                return await self._line_message_service.send_location_quick_reply(reply_token, user_id)

            response_text = result.text or "抱歉，我無法理解您的問題，請重新輸入。"
            success = await self._line_message_service.send_line_reply(reply_token, response_text, user_id)

            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            return success

        except ValueError as e:
            logger.error(f"API error in processing message: {e}")
            fallback = f"抱歉，AI 服務暫時無法使用：{e}"
            await self._line_message_service.send_line_reply(reply_token, fallback, user_id)
            return False

        except Exception as e:
            logger.error(f"Error in processing message: {e}", exc_info=True)
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            await self._line_message_service.send_line_reply(reply_token, error_message, user_id)
            return False
