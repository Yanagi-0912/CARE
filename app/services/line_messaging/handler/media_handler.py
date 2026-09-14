import logging
from pathlib import Path

from linebot.v3.webhooks import (
    MessageEvent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    reset_request_language,
    set_request_language,
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
        # 辨識語音之前就要知道使用者的語言：選台語的走台語 STT，其他語言交給
        # faster-whisper 當提示。語言原本要到 _process_and_reply 讀了 profile 才
        # 設定，那時辨識早就做完了——所有語音都是用預設的 zh-TW 辨識的
        # （2026-09-14 發現，ba9bf1b 的語言提示因此從沒生效）。
        lang_token = set_request_language(await self._language_choice_for(user_id))
        try:
            user_text, message_type, image_text = await self._extract_media_text(
                message, user_id
            )
        finally:
            reset_request_language(lang_token)
        await self._process_and_reply(
            event, user_text, message_type, image_text=image_text
        )

    async def _language_choice_for(self, user_id: str) -> str:
        """讀使用者設定的語言（含台語）；讀不到就用預設，不擋辨識。"""
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.warning("辨識前讀取使用者語言失敗，以預設語言辨識", exc_info=True)
            return DEFAULT_USER_LANGUAGE
        return self._language_choice_from_profile(profile)

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
        if media_type == "audio":
            # 語音逐字稿就是使用者親口問的問題，要跟打字一樣進 agent。包上媒體前綴的話，
            # agent 會把它當成圖片／文件抽出的全文而禁止查知識庫（nodes.py 的
            # _is_media_extracted_content 與 prompt 規則 (e)）；2026-09-14 用語音問
            # 「肚子痛的原因是什麼啊」就因此沒有查知識庫。語音回覆看的是 message_type。
            return cleaned_content, media_type, image_text
        return (
            f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}",
            media_type,
            image_text,
        )
