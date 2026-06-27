from pathlib import Path
from typing import Optional
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
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage
import logging

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg",
}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


class LineEventHandler:
    """
    處理 webhook 接受到的 event 並分發到對應的 service。
    """

    def __init__(self, agent, line_message_service, chat_history_repository):
        self._agent = agent
        self._line_message_service = line_message_service
        self._chat_history_repository = chat_history_repository

    async def handle(self, event: MessageEvent) -> None:
        """對外進入點：驗證 → 提取內容 → 呼叫 Agent → 回覆"""
        event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        user_id = getattr(event.source, "user_id", None)
        validate_reply_context(event.reply_token, user_id)

        result = await self._extract_content(event.message, user_id)
        if result is None:
            return

        user_text, message_type = result
        await self._invoke_and_reply(
            user_text=user_text,
            reply_token=event.reply_token,
            user_id=user_id,
            message_type=message_type,
            event_time=event_time,
        )

    async def _extract_content(
        self, message, user_id: Optional[str]
    ) -> Optional[tuple[str, str]]:
        """
        依訊息類型提取純文字內容與類型標籤。
        回傳 (user_text, message_type)，不支援的類型回傳 None。
        """
        if isinstance(message, TextMessageContent):
            logger.info(f"Received text message from user {user_id}")
            return validate_text_message(message.text), "text"

        if isinstance(message, LocationMessageContent):
            logger.info(
                f"Received location from user {user_id}: "
                f"({message.latitude}, {message.longitude})"
            )
            return f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}", "location"

        if isinstance(
            message,
            (ImageMessageContent, VideoMessageContent, AudioMessageContent, FileMessageContent),
        ):
            media_id = message.id
            media_type = message.type
            file_name = getattr(message, "file_name", None)

            if media_type == "file" and file_name:
                media_type = self._infer_media_type_from_file_name(file_name)
            validate_media_message(media_id, media_type, file_name)

            log_msg = f"Received {media_type} message from user {user_id}"
            if file_name:
                log_msg += f": {file_name}"
            logger.info(log_msg)

            media_content = await media_processor_service.process_media(
                media_message_id=media_id,
                user_media_type=media_type,
                source_file_name=file_name,
                user_id=user_id,
            )
            return f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}", media_type

        logger.warning(f"Unsupported message type: {type(message).__name__}")
        return None

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

    async def _build_agent_messages(
        self,
        user_id: Optional[str],
        current_content: str,
        message_type: str,
    ) -> list[AnyMessage]:
        """從對話暫存庫載入歷史紀錄，並轉換為 LangChain 訊息格式。"""
        if not user_id:
            return [HumanMessage(content=current_content)]

        history = await self._chat_history_repository.list_messages(user_id)
        langchain_messages: list[AnyMessage] = [
            AIMessage(content=msg.content)
            if msg.message_type == "assistant_reply"
            else HumanMessage(content=msg.content)
            for msg in history
        ]

        # 位置訊息（location）未寫入 Redis，需手動追加至尾端
        if message_type == "location":
            langchain_messages.append(HumanMessage(content=current_content))

        # 防禦性處理：確保至少包含當前輸入
        if not langchain_messages:
            langchain_messages.append(HumanMessage(content=current_content))

        return langchain_messages

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

            # 2. 從對話暫存庫載入包含當前訊息的歷史上下文
            agent_messages = await self._build_agent_messages(
                user_id, user_text, message_type
            )

            # 呼叫 Agent 進行決策（在 context scope 內）
            with consultation_context_scope(context):
                result = await self._agent.invoke(
                    user_input=user_text, messages=agent_messages
                )

            response_text = (
                result.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = result.get("call_request_location", False)
            kwargs = {"request_location": True} if call_request_location else {}
            success = await self._line_message_service.send_line_reply(
                reply_token, response_text, user_id, **kwargs
            )

            # 3. 成功送出後記錄 Assistant 訊息 (排除 location)
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
            await self._line_message_service.send_line_reply(
                reply_token, "抱歉，處理您的訊息時發生錯誤，請稍後再試", user_id
            )
            return False
