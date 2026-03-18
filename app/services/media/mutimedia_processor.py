from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from datetime import datetime
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
import secrets
import requests

logger = logging.getLogger(__name__)

# 媒體解析 webhook URL
MEDIA_PARSE_WEBHOOK_URL = "http://localhost:5678/webhook/bff1fd27-efc4-45cf-b64a-adb0475aa35c"
WEBHOOK_TIMEOUT_SECONDS = 30

# 媒體暫存目錄：所有下載檔案都先落在這裡，並在 finally 進行清理
TMP_DIR = Path("app_data") / "tmp"

# 單檔下載大小上限10MB
MAX_MEDIA_SIZE_BYTES = 10 * 1024 * 1024

# 允許處理的媒體類型白名單，其他一律拒絕
ALLOWED_MEDIA_TYPES = {"image", "video", "audio", "file"}

# 各媒體類型對應的 MIME 前綴，用於驗證下載來源回傳內容是否合理
EXPECTED_MIME_PREFIXES = {
    "image": ("image/",),
    "video": ("video/",),
    "audio": ("audio/",),
    "file": ("application/", "text/"),
}
# URL 未提供副檔名時的保底對應值
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
            temp_file_path = self._download_media_to_tmp(user_media, user_media_type)
            # 以 form-data 上傳檔案給 webhook，取回解析後文字。
            user_text = self._extract_user_text_via_webhook(temp_file_path)

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
            # 不論成功或失敗都嘗試清理，避免暫存檔堆積。
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
        # 媒體類型白名單過濾，阻擋未知類型。
        normalized_type = media_type.lower().strip()
        if normalized_type not in ALLOWED_MEDIA_TYPES:
            raise ValueError(f"Unsupported media type: {media_type}")

        # 僅允許 https，避免 file:// 等本機路徑或危險 schema。
        parsed = urlparse(media_url)
        if parsed.scheme not in {"https"}:
            raise ValueError("Invalid media URL scheme")

        #優先使用 URL 上原始副檔名，若缺失再使用保底副檔名。
        original_extension = Path(parsed.path).suffix.lower()
        extension = original_extension or MEDIA_EXTENSIONS.get(normalized_type, ".bin")

        #產生可追蹤且低碰撞風險的檔名：類型_時間戳_隨機碼。
        safe_type = "".join(ch for ch in normalized_type if ch.isalnum()) or "media"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = secrets.token_hex(4)

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        target = TMP_DIR / f"{safe_type}_{timestamp}_{random_suffix}{extension}"
        with requests.get(media_url, timeout=20, stream=True) as response:
            response.raise_for_status()

            content_length_header = response.headers.get("Content-Length")
            if content_length_header:
                try:
                    content_length = int(content_length_header)
                except ValueError as exc:
                    raise ValueError("Invalid Content-Length header") from exc
                if content_length > MAX_MEDIA_SIZE_BYTES:
                    raise ValueError(
                        f"Media too large: {content_length} bytes > {MAX_MEDIA_SIZE_BYTES} bytes"
                    )

            # 驗證回應 MIME 與宣稱 media_type 大致一致，降低內容偽裝風險。
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            expected_prefixes = EXPECTED_MIME_PREFIXES[normalized_type]
            if content_type and not any(content_type.startswith(prefix) for prefix in expected_prefixes):
                raise ValueError(
                    f"Unexpected content type '{content_type}' for media type '{normalized_type}'"
                )

            downloaded_size = 0
            with target.open("wb") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    downloaded_size += len(chunk)
                    # 下載中仍要檢查，防止 Content-Length 缺失或不可信。
                    if downloaded_size > MAX_MEDIA_SIZE_BYTES:
                        raise ValueError(
                            f"Media too large while downloading: {downloaded_size} bytes > {MAX_MEDIA_SIZE_BYTES} bytes"
                        )
                    temp_file.write(chunk)

        logger.info(f"Downloaded media from URL to {target}")
        return target

    def _extract_user_text_via_webhook(self, file_path: Path) -> str:
        """Upload file via form-data (key=file) and return parsed text from webhook."""
        if not MEDIA_PARSE_WEBHOOK_URL:
            logger.warning("MEDIA_PARSE_WEBHOOK_URL is empty; using placeholder extracted text")
            return "Test text extracted from media"

        with file_path.open("rb") as media_file:
            files = {
                "file": (
                    file_path.name,
                    media_file,
                    "application/octet-stream",
                )
            }
            response = requests.post(
                MEDIA_PARSE_WEBHOOK_URL,
                files=files,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        parsed_text = ""
        if "application/json" in content_type:
            payload: Any = response.json()
            if isinstance(payload, dict):
                parsed_text = str(
                    payload.get("user_text")
                    or payload.get("text")
                    or payload.get("result")
                    or ""
                ).strip()
        else:
            parsed_text = response.text.strip()

        if not parsed_text:
            raise ValueError("Webhook returned empty parsed text")

        return parsed_text

    def _cleanup_temp_file(self, file_path: Path) -> None:
        """Best-effort temp file cleanup; never raise to main flow."""
        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleaned up temp file: {file_path}")
        except Exception as e:
            # 清理失敗不影響主流程，只記錄警告供後續排查。
            logger.warning(f"Failed to cleanup temp file {file_path}: {e}")


media_processor_service = MediaProcessorService()