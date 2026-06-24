from pathlib import Path
from typing import Optional, cast
from datetime import datetime, timezone
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)

from app.services.media.mutimedia_processor import media_processor_service
from app.services.line_messaging.shared.validation import (
    validate_media_message,
    validate_reply_context,
    validate_text_message,
)
from app.services.consultation.context import (
    ConsultationContext,
    consultation_context_scope,
)
from app.models.chat_message import ChatMessage
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
    """
    處理 webhook 接受到的 event 並分發到對應的 service
    """

    def __init__(
        self,
        agent,
        line_message_service,
        chat_history_repository,
    ):
        self._agent = agent
        self._line_message_service = line_message_service
        self._chat_history_repository = chat_history_repository

    async def handle(self, event: MessageEvent) -> None:
        """
        對外進入點
        """

        event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        user_id = getattr(event.source, "user_id", None)
        validate_reply_context(event.reply_token, user_id)

        reply_token = event.reply_token
        message = event.message

        if isinstance(message, TextMessageContent):
            await self._handle_text_message(message, reply_token, user_id, event_time)
        elif isinstance(message, LocationMessageContent):
            await self._handle_location_message(
                message, reply_token, user_id, event_time
            )
        elif isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            await self._handle_media_message(message, reply_token, user_id, event_time)
        else:
            logger.warning(f"Unsupported message type: {type(message).__name__}")

    async def _handle_text_message(
        self,
        message: TextMessageContent,
        reply_token: str,
        user_id: Optional[str],
        event_time: datetime,
    ) -> None:
        """
        處理文字訊息
        """
        logger.info(f"Received text message event from user {user_id}")
        user_text = validate_text_message(message.text)

        await self._invoke_and_reply(
            user_text=user_text,
            reply_token=reply_token,
            user_id=user_id,
            message_type="text",
            event_time=event_time,
        )

    async def _handle_location_message(
        self,
        message: LocationMessageContent,
        reply_token: str,
        user_id: Optional[str],
        event_time: datetime,
    ) -> None:
        """處理位置訊息"""
        lat: float = message.latitude
        lng: float = message.longitude

        logger.info(f"Received location from user {user_id}: ({lat}, {lng})")

        # 將位置資訊轉為純文字，讓 LangGraph Agent 決定如何使用 (例如觸發 find_nearby_hospitals)
        location_text = f"這是我的目前位置：lat={lat}, lng={lng}"

        await self._invoke_and_reply(
            user_text=location_text,
            reply_token=reply_token,
            user_id=user_id,
            message_type="location",
            event_time=event_time,
        )

    async def _handle_media_message(
        self, message, reply_token: str, user_id: Optional[str], event_time: datetime
    ) -> None:
        """處理多媒體訊息"""
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

        await self._invoke_and_reply(
            user_text=f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}",
            reply_token=reply_token,
            user_id=user_id,
            message_type=media_type,
            event_time=event_time,
        )

    @staticmethod
    def _infer_media_type_from_file_name(file_name: str) -> str:
        """根據副檔名推斷媒體類型"""
        extension = Path(file_name).suffix.lower()
        if extension in IMAGE_FILE_EXTENSIONS:
            return "image"
        if extension in VIDEO_FILE_EXTENSIONS:
            return "video"
        if extension in AUDIO_FILE_EXTENSIONS:
            return "audio"
        return "file"

    async def _invoke_and_reply(
        self,
        user_text: str,
        reply_token: str,
        user_id: Optional[str] = None,
        message_type: str = "text",
        event_time: Optional[datetime] = None,
    ) -> bool:
        """呼叫 Agent 並回覆訊息"""
        try:
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")

            context = ConsultationContext(
                line_id=user_id,
                message_type=message_type,
                event_time=event_time,
            )

            # 1. 記錄 User 訊息到 ChatHistoryRepository (排除 location)
            if user_id and message_type != "location":
                user_msg = ChatMessage(
                    line_id=user_id,
                    message_type=message_type,
                    content=user_text,
                    timestamp=event_time or datetime.now(timezone.utc),
                )
                await self._chat_history_repository.append_message(user_id, user_msg)

            # 呼叫 Agent 進行決策（在 context scope 內）
            with consultation_context_scope(context):
                result = await self._agent.invoke(user_input=user_text)

            # 回傳 Agent 最終產出的文字回覆
            response_text = (
                result.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = result.get("call_request_location", False)
            kwargs = {}
            if call_request_location:
                kwargs["request_location"] = True
            success = await self._line_message_service.send_line_reply(
                reply_token, response_text, user_id, **kwargs
            )

            # 2. 成功送出後記錄 Assistant 訊息 (排除 location)
            if success and user_id and message_type != "location":
                assistant_msg = ChatMessage(
                    line_id=user_id,
                    message_type="assistant_reply",
                    content=response_text,
                    timestamp=datetime.now(timezone.utc),
                )
                await self._chat_history_repository.append_message(user_id, assistant_msg)

            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            return success

        except Exception as e:
            logger.error(f"Error in processing message: {e}", exc_info=True)
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            await self._line_message_service.send_line_reply(
                reply_token, error_message, user_id
            )
            return False
