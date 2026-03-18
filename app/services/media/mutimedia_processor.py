from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from app.services.gemini_service import GeminiService
from app.services.line.token_manager import line_token_manager
import logging
import requests

logger = logging.getLogger(__name__)
TMP_DIR = Path("app_data") / "tmp"
MEDIA_EXTENSIONS = {
    "image": ".jpg",
    "video": ".mp4",
    "audio": ".m4a",
    "file": ".bin",
}


class MediaProcessorService:
    """Handle incoming LINE text and send replies based on Gemini tool output."""

    def __init__(self):
        self.gemini_service = GeminiService()
        logger.info("MediaProcessorService initialized with Gemini AI")

    async def process_and_reply(
        self, user_media: str, user_media_type: str, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        temp_file_path = None
        try:
            logger.info(f"Processing {user_media_type} message from user {user_id}...")
            # TODO: validate media type/size before download to avoid abuse and high cost.
            temp_file_path = self._download_media_to_tmp(user_media, user_media_type)
            # TODO: process media via n8n workflow with timeout/retry and failure fallback.
            user_text = "Test text extracted from media"
            result = await self.gemini_service.generate_response_with_tools(user_text)
            response_text = result.text or "抱歉，我無法理解您的問題，請重新輸入。"
            success = await self._send_line_reply(reply_token, response_text, user_id)

            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            return success

        except ValueError as e:
            logger.error(f"API error in process_and_reply: {e}")
            fallback = f"抱歉，AI 服務暫時無法使用：{e}"
            await self._send_line_reply(reply_token, fallback, user_id)
            return False

        except Exception as e:
            logger.error(f"Error in process_and_reply: {e}", exc_info=True)
            await self._send_error_reply(reply_token, user_id)
            return False
        finally:
            if temp_file_path:
                self._cleanup_temp_file(temp_file_path)

    async def _send_line_reply(
        self, reply_token: str, message_text: str, user_id: Optional[str] = None
    ) -> bool:
        """Send a plain text reply to LINE using the current channel access token."""
        try:
            access_token = line_token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[
                            TextMessage(
                                text=message_text,
                                quickReply=None,
                                quoteToken=None,
                            )
                        ],
                        notificationDisabled=False,
                    )
                )

            logger.info(f"Message sent to LINE for user {user_id}")
            return True

        except ValueError as e:
            logger.error(f"Failed to get LINE token: {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to send LINE message: {e}", exc_info=True)
            return False

    async def _send_error_reply(
        self, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        """Send generic fallback message when unexpected exception occurs."""
        try:
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            return await self._send_line_reply(reply_token, error_message, user_id)
        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")
            return False

    def _download_media_to_tmp(self, media_url: str, media_type: str) -> Path:
        parsed = urlparse(media_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Invalid media URL scheme")

        original_extension = Path(parsed.path).suffix.lower()
        extension = original_extension or MEDIA_EXTENSIONS.get(media_type.lower(), ".bin")

        file_stem = Path(parsed.path).stem or "media"
        safe_stem = "".join(ch for ch in file_stem if ch.isalnum() or ch in {"-", "_"})
        if not safe_stem:
            safe_stem = "media"

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        target = TMP_DIR / f"{safe_stem}{extension}"

        response = requests.get(media_url, timeout=20)
        response.raise_for_status()
        target.write_bytes(response.content)
        logger.info(f"Downloaded media from URL to {target}")
        return target

    def _cleanup_temp_file(self, file_path: Path) -> None:
        """Best-effort temp file cleanup; never raise to main flow."""
        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleaned up temp file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp file {file_path}: {e}")


media_processor_service = MediaProcessorService()