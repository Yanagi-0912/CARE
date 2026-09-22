import logging
from pathlib import Path

from linebot.v3.webhooks import (
    MessageEvent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from app.core.request_logging import log_stage
from app.core.user_language import (
    get_detected_speech_language,
    normalize_user_language,
    reset_detected_speech_language,
    reset_request_language,
    set_detected_speech_language,
    set_request_language,
)
from app.i18n.messages import t
from app.services.media.mutimedia_processor import (
    MAX_MEDIA_SIZE_BYTES,
    NO_CONTENT_TEXT,
    MediaServiceUnavailableError,
    MediaTooLargeError,
    MediaUnsupportedError,
    media_processor_service,
)
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
        lost_location_service=None,
        urgency_classifier=None,
        clinic_recording_flow=None,
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
            lost_location_service=lost_location_service,
            urgency_classifier=urgency_classifier,
        )
        self._user_document_ingest_service = user_document_ingest_service
        # 看診錄音改在聊天室錄（app/services/clinic_transcript/line_flow.py）。
        # 沒注入時語音一律當問題，跟以前一樣。
        self._clinic_recording_flow = clinic_recording_flow

    async def handle(
        self, event: MessageEvent, *, user_profile: dict | None = None
    ) -> None:
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
        # 讀取動畫在辨識之前就要開：下載＋n8n／STT 動輒 5–20 秒，以前要等辨識
        # 完、進 _process_and_reply 才開，使用者這段時間看到的是已讀不回。
        # _process_and_reply 會再開一次，那一次順便把 60 秒的窗口從 agent 開始
        # 重新算，所以這裡不用擔心辨識太久動畫先消失。
        # 背景開，不等它（見 _schedule_loading_animation）。
        self._schedule_loading_animation(user_id)
        # 辨識語音之前就要知道使用者的語言：說中文的人同時送台語 STT 與 Gemini
        # 再選一份，其他語言只送 Gemini（備援 faster-whisper）並把語言當提示。
        # 語言原本要到 _process_and_reply 讀了 profile 才設定，那時辨識早就做完了
        # ——所有語音都是用預設的 zh-TW 辨識的（2026-09-14 發現，ba9bf1b 的語言
        # 提示因此從沒生效）。
        if user_profile is None:
            user_profile = await self._load_profile_or_none(user_id)
        language_choice = self._language_choice_from_profile(user_profile)
        if await self._handled_as_clinic_recording(event, user_id, language_choice):
            return
        lang_token = set_request_language(language_choice)
        # 語音實際聽出來的語言由辨識那邊寫進來（見 mutimedia_processor
        # ._transcribe_zh_or_taiwanese）。先清乾淨，免得讀到別的請求留下的值。
        detected_token = set_detected_speech_language(None)
        try:
            user_text, message_type, image_text = await self._extract_media_text(
                message, user_id, language=normalize_user_language(language_choice)
            )
            detected_speech_language = get_detected_speech_language()
        finally:
            reset_detected_speech_language(detected_token)
            reset_request_language(lang_token)
        await self._process_and_reply(
            event,
            user_text,
            message_type,
            image_text=image_text,
            speech_language=detected_speech_language,
            user_profile=user_profile,
        )

    async def _handled_as_clinic_recording(
        self, event: MessageEvent, user_id: str, language_choice: str
    ) -> bool:
        """這則語音／音檔是看診錄音就交給看診錄音流程，不進 agent。"""
        flow = self._clinic_recording_flow
        message = event.message
        if flow is None or not user_id:
            return False
        if isinstance(message, AudioMessageContent):
            is_file, file_name = False, None
        elif isinstance(message, FileMessageContent) and (
            Path(message.file_name or "").suffix.lower() in AUDIO_FILE_EXTENSIONS
        ):
            is_file, file_name = True, message.file_name
        else:
            return False
        try:
            return await flow.handle_audio(
                user_id,
                getattr(event, "reply_token", ""),
                normalize_user_language(language_choice),
                message_id=message.id,
                is_file=is_file,
                file_name=file_name,
                duration_ms=getattr(message, "duration", None),
                file_size=getattr(message, "file_size", None),
            )
        except Exception:  # noqa: BLE001 - 看診錄音那條路壞了，至少讓語音照常被回答
            logger.exception("stage=clinic_chat 判斷是否為看診錄音時出錯，照一般語音處理")
            return False

    async def _load_profile_or_none(self, user_id: str) -> dict | None:
        """dispatcher 沒把檔案帶下來時自己讀；讀不到回 None（語言用預設），不擋辨識。

        回 None 時 _process_and_reply 會再讀一次——那是「Mongo 剛剛壞掉」的
        邊緣情況，多一次讀取換來與以前相同的失敗行為（那邊讀不到就回錯誤）。
        """
        if not self._user_profile_service or not user_id:
            return None
        try:
            return await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.warning("辨識前讀取使用者語言失敗，以預設語言辨識", exc_info=True)
            return None

    async def _extract_media_text(
        self, message, user_id: str, language: str | None = None
    ) -> tuple[str, str, str]:
        """回傳 (給 agent 的文字, 媒體類型, 圖片辨識原文)。

        `language` 是錯誤說明用的語言；沒給就用 request context 的語言。

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

        # 四種失敗分開講（理由見 mutimedia_processor.MediaProcessingError）。
        # LineValidationError 由 dispatcher 原句回給使用者，所以這裡就要譯好。
        kind = t(f"media.kind.{media_type}", language=language)
        try:
            media_content = await media_processor_service.process_media(
                media_message_id=media_id,
                user_media_type=media_type,
                source_file_name=file_name,
                user_id=user_id,
            )
        except MediaTooLargeError as exc:
            log_stage(logger, "media_rejected", reason="too_large", bytes=exc.size_bytes)
            raise LineValidationError(
                t("media.too_large", language=language).format(
                    kind=kind, limit_mb=MAX_MEDIA_SIZE_BYTES // (1024 * 1024)
                )
            ) from exc
        except MediaUnsupportedError as exc:
            log_stage(logger, "media_rejected", reason="unsupported", detail=str(exc)[:80])
            raise LineValidationError(
                t("media.unsupported", language=language).format(kind=kind)
            ) from exc
        except MediaServiceUnavailableError as exc:
            log_stage(logger, "media_failed", reason="service", detail=str(exc)[:120])
            raise LineValidationError(
                t("media.service_unavailable", language=language).format(kind=kind)
            ) from exc

        cleaned_content = (media_content or "").strip()
        # NO_CONTENT_TEXT 是 processor 在「辨識成功但沒有內容」時的哨兵
        # （靜音的語音、空白的圖）；舊版 n8n 也會回同一個開頭的句子。
        if (
            not cleaned_content
            or cleaned_content == NO_CONTENT_TEXT
            or cleaned_content.startswith("Unable to extract text")
        ):
            log_stage(logger, "media_rejected", reason="no_content")
            raise LineValidationError(
                t("media.no_content", language=language).format(kind=kind)
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
