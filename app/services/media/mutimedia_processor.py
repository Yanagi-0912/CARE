from pathlib import Path
from typing import Any, Optional
from datetime import datetime
import mimetypes
from app.core.config import settings
from app.services.gemini import GeminiService
from app.services.line.token_manager import line_token_manager
import logging
import secrets
import requests

logger = logging.getLogger(__name__)

# 媒體解析 webhook URL
MEDIA_PARSE_WEBHOOK_URL = settings.MEDIA_PARSE_WEBHOOK_URL
WEBHOOK_TIMEOUT_SECONDS = 30
LINE_MESSAGE_CONTENT_API = (
    "https://api-data.line.me/v2/bot/message/{message_id}/content"
)

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
        self.gemini_service = GeminiService(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.MODEL_NAME,
        )
        logger.info("MediaProcessorService initialized with Gemini AI")

    async def process_media(
        self,
        media_message_id: str,
        user_media_type: str,
        source_file_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        temp_file_path = None
        try:
            logger.info(f"Processing {user_media_type} message from user {user_id}...")
            temp_file_path = self._download_media_to_tmp(
                media_message_id,
                user_media_type,
                source_file_name=source_file_name,
            )
            user_text = self._extract_user_text_via_webhook(temp_file_path)
            # TODO: 清洗user_text，移除不必要的空白或控制字元，確保回覆格式整潔。
            logger.info(f"Successfully processed and replied to user {user_id}")
            return user_text

        except Exception as e:
            logger.error(f"Error in process_media: {e}", exc_info=True)
            return "發生錯誤，請忽略此內容或重新嘗試。"
        finally:
            # 不論成功或失敗都嘗試清理，避免暫存檔堆積。
            if temp_file_path:
                self._cleanup_temp_file(temp_file_path)

    def _download_media_to_tmp(
        self,
        media_message_id: str,
        media_type: str,
        source_file_name: Optional[str] = None,
    ) -> Path:
        # 媒體類型白名單過濾，阻擋未知類型。
        normalized_type = media_type.lower().strip()
        if normalized_type not in ALLOWED_MEDIA_TYPES:
            raise ValueError(f"Unsupported media type: {media_type}")

        if not media_message_id:
            raise ValueError("Missing media message id")

        extension = Path(source_file_name).suffix.lower() if source_file_name else ""
        if not extension:
            extension = MEDIA_EXTENSIONS.get(normalized_type, ".bin")

        # 產生可追蹤且低碰撞風險的檔名：類型_時間戳_隨機碼。
        safe_type = "".join(ch for ch in normalized_type if ch.isalnum()) or "media"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = secrets.token_hex(4)

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        target = TMP_DIR / f"{safe_type}_{timestamp}_{random_suffix}{extension}"

        access_token = line_token_manager.get_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        content_url = LINE_MESSAGE_CONTENT_API.format(message_id=media_message_id)

        with requests.get(
            content_url, headers=headers, timeout=20, stream=True
        ) as response:
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
            content_type = (
                response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            )
            expected_prefixes = EXPECTED_MIME_PREFIXES[normalized_type]
            if content_type and not any(
                content_type.startswith(prefix) for prefix in expected_prefixes
            ):
                raise ValueError(
                    f"Unexpected content type '{content_type}' for media type '{normalized_type}'"
                )

            if extension == ".bin":
                guessed_extension = mimetypes.guess_extension(content_type)
                if guessed_extension:
                    extension = guessed_extension.lower()
                    target = target.with_suffix(extension)

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

        logger.info(f"Downloaded media content from LINE API to {target}")
        return target

    def _extract_user_text_via_webhook(self, file_path: Path) -> str:
        """Upload file via form-data (key=file) and return parsed text from webhook."""
        if not MEDIA_PARSE_WEBHOOK_URL:
            logger.warning(
                "MEDIA_PARSE_WEBHOOK_URL is empty; using placeholder extracted text"
            )
            return "Test text extracted from media"

        mime_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )

        try:
            with file_path.open("rb") as media_file:
                files = {
                    "file": (
                        file_path.name,
                        media_file,
                        mime_type,
                    )
                }
                response = requests.post(
                    MEDIA_PARSE_WEBHOOK_URL,
                    files=files,
                    timeout=WEBHOOK_TIMEOUT_SECONDS,
                )

            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Webhook request failed: {e}")
            raise ValueError(f"Failed to reach webhook: {e}") from e

        content_type = response.headers.get("Content-Type", "").lower()
        response_text = response.text.strip()

        logger.debug(
            f"Webhook response: status={response.status_code}, "
            f"content_type={content_type}, text_length={len(response_text)}"
        )

        parsed_text = ""
        if not response_text:
            logger.warning("Webhook returned empty response body")
            return "Unable to extract text from media file (empty webhook response)"

        if "application/json" in content_type:
            try:
                payload: Any = response.json()
                if isinstance(payload, dict):
                    parsed_text = str(
                        payload.get("user_text")
                        or payload.get("text")
                        or payload.get("result")
                        or ""
                    ).strip()
                elif isinstance(payload, str):
                    parsed_text = payload.strip()
                else:
                    logger.warning(f"Unexpected JSON payload type: {type(payload)}")
            except requests.exceptions.JSONDecodeError as e:
                logger.error(
                    f"Failed to parse webhook JSON response: {e}, text={response_text[:200]}"
                )
                return f"Unable to extract text from media (invalid JSON from webhook)"
        else:
            parsed_text = response_text

        if not parsed_text:
            logger.warning("Webhook returned no extractable text content")
            return "Unable to extract text from media file (no content extracted)"

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
