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
from app.dependencies import get_line_message_service
from app.services.medical.medical_service import (
    medical_service,
    format_facility_list,
    NO_FACILITY_MESSAGE,
)
from app.services.media.mutimedia_processor import media_processor_service
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


class LineEventContext:
    def __init__(self, event: MessageEvent):
        if not event.reply_token:
            logger.warning("Missing reply_token in LINE event")
            raise ValueError("Missing reply_token in LINE event")

        user_id = getattr(event.source, "user_id", None)
        if not user_id:
            logger.warning("Missing user_id in LINE event")
            raise ValueError("Missing user_id in LINE event")

        self.event = event
        self.reply_token = event.reply_token
        self.user_id = user_id
        self.message = event.message

    async def dispatch(self) -> None:
        message = self.message

        if isinstance(message, TextMessageContent):
            await self.handle_text_message()
        elif isinstance(message, LocationMessageContent):
            await self.handle_location_message()
        elif isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            await self.handle_media_message()
        else:
            logger.warning(f"Unsupported message type: {type(message).__name__}")

    async def handle_text_message(self) -> None:
        logger.info(f"Received text message event from user {self.user_id}")
        message = cast(TextMessageContent, self.message)

        line_message_service = get_line_message_service()
        await line_message_service.process_and_reply(
            user_text=message.text,
            reply_token=self.reply_token,
            user_id=self.user_id,
        )

    async def handle_location_message(self) -> None:
        message = cast(LocationMessageContent, self.message)
        lat: float = message.latitude
        lng: float = message.longitude

        logger.info(f"Received location from user {self.user_id}: ({lat}, {lng})")

        facilities = await medical_service.handle_location(self.user_id, lat, lng)
        if facilities is None:
            return

        reply_text = (
            format_facility_list(facilities) if facilities else NO_FACILITY_MESSAGE
        )
        line_message_service = get_line_message_service()
        await line_message_service.send_line_reply(
            self.reply_token, reply_text, self.user_id
        )

    async def handle_media_message(self) -> None:
        media_id = self.message.id
        media_type = self.message.type
        file_name = getattr(self.message, "file_name", None)

        if media_type == "file" and file_name:
            media_type = self._infer_media_type_from_file_name(file_name)

        log_msg = f"Received {media_type} message event from user {self.user_id}"
        if file_name:
            log_msg += f": {file_name}"
        logger.info(log_msg)

        media_content = await media_processor_service.process_media(
            media_message_id=media_id,
            user_media_type=media_type,
            source_file_name=file_name,
            user_id=self.user_id,
        )

        line_message_service = get_line_message_service()
        await line_message_service.process_and_reply(
            user_text=f"以下為用戶傳送的{media_type}媒體內容：\n{media_content}",
            reply_token=self.reply_token,
            user_id=self.user_id,
        )

    # 應用在media部分 根據副檔名推斷媒體類型
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
