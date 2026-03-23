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
from app.services.line.message_service import line_message_service
from app.services.medical.medical_service import medical_service, session_store
from app.services.media.mutimedia_processor import media_processor_service
from app.schemas import MedicalFacility
import logging

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"}
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

    # 根據副檔名推斷媒體類型
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

    # 私有方法(前綴的底線"_")，處理多媒體與檔案訊息
    async def _process_media_and_reply(
        self,
        media_message_id: str,
        user_media_type: str,
        source_file_name: Optional[str] = None,
    ) -> None:
        media_content = await media_processor_service.process_media(
            media_message_id=media_message_id,
            user_media_type=user_media_type,
            source_file_name=source_file_name,
            user_id=self.user_id,
        )

        await line_message_service.process_and_reply(
            user_text=f"以下為用戶傳送的{user_media_type}媒體內容：\n{media_content}",
            reply_token=self.reply_token,
            user_id=self.user_id,
        )

    async def handle_text_message(self) -> None:
        logger.info(f"Received text message event from user {self.user_id}")
        message = cast(TextMessageContent, self.message)

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

        if session_store.get(self.user_id) != "WAITING_LOCATION":
            logger.warning(
                f"User {self.user_id} sent location but was not in WAITING_LOCATION state, ignoring."
            )
            return

        session_store.clear(self.user_id)

        facilities: list[MedicalFacility] = await medical_service.find_nearby_hospitals(
            lat, lng
        )

        if facilities:
            lines = [f"為您找到附近 {len(facilities)} 間醫療院所：\n"]
            for i, f in enumerate(facilities, 1):
                dist = (
                    f"（{f.distance_meters:.0f} 公尺）"
                    if f.distance_meters is not None
                    else ""
                )
                lines.append(f"{i}. {f.name}{dist}\n   {f.address}")
            reply_text = "\n".join(lines)
        else:
            reply_text = (
                "抱歉，您附近 1 公里內暫時找不到醫療院所資料。\n功能仍在建置中，敬請期待！"
            )

        await line_message_service.send_line_reply(self.reply_token, reply_text, self.user_id)

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

        await self._process_media_and_reply(
            media_message_id=media_id,
            user_media_type=media_type,
            source_file_name=file_name,
        )