import logging
from pathlib import Path

from linebot.v3.webhooks import (
    MessageEvent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from app.services.media.mutimedia_processor import media_processor_service
from app.services.line_messaging.handler.message_handler import (
    BaseLineMessageHandler,
    LineValidationError,
)

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


class LineMediaHandler(BaseLineMessageHandler):
    """處理多媒體訊息事件（圖片、影片、語音、檔案）。"""

    def __init__(
        self,
        agent,
        history_service,
        user_profile_service,
        replier,
        loading_animation_service=None,
        user_document_ingest_service=None,
        safety_alert_service=None,
        emergency_family_alert_service=None,
    ):
        # 語音、圖片、檔案抽出的文字同樣會過急迫度判斷；沒有把通報服務傳下去的話，
        # 當事人收得到紅卡，家人卻收不到通報——而長輩最常用的正是語音。
        super().__init__(
            agent,
            history_service,
            user_profile_service,
            replier,
            loading_animation_service,
            safety_alert_service,
            emergency_family_alert_service,
        )
        self._user_document_ingest_service = user_document_ingest_service

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            raise ValueError("Expected Media Message Content")

        user_id = getattr(event.source, "user_id", "")
        user_text, message_type, image_text = await self._extract_media_text(
            message, user_id
        )
        await self._process_and_reply(
            event, user_text, message_type, image_text=image_text
        )

    async def _extract_media_text(
        self, message, user_id: str
    ) -> tuple[str, str, str]:
        """回傳 (給 agent 的文字, 媒體類型, 圖片辨識原文)。

        第三項只有圖片才有值，其餘是空字串：n8n 裡只有圖片走影像解析的 prompt，
        表格卡吃的「值（註記）」Markdown 表格是那個 prompt 產的，文件與語音走的
        是其他解析節點。它是未加前綴的原文——前綴那行會被表格卡當成標題。
        """
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

        if (
            media_type == "file"
            and user_id
            and self._user_document_ingest_service is not None
        ):
            try:
                await self._user_document_ingest_service.ingest_text(
                    user_id,
                    media_content,
                    source_name=file_name or "upload",
                    media_type=media_type,
                )
            except Exception:
                logger.exception(
                    "Failed to ingest user document for user_id=%s source=%s",
                    user_id,
                    file_name or "upload",
                )

        image_text = media_content if media_type == "image" else ""
        return (
            f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}",
            media_type,
            image_text,
        )
