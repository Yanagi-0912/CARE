from typing import Optional

from app.infrastructure.line.shared.errors import LineValidationError

MAX_TEXT_LENGTH = 5000
ALLOWED_MEDIA_TYPES = {"image", "video", "audio", "file"}


def validate_reply_context(reply_token: str, user_id: Optional[str]) -> None:
    if not reply_token or not reply_token.strip():
        raise LineValidationError("LINE 事件缺少 reply_token")
    if not user_id or not user_id.strip():
        raise LineValidationError("LINE 事件缺少 user_id")


def validate_text_message(text: str) -> str:
    normalized_text = text.strip()
    if not normalized_text:
        raise LineValidationError("LINE 文字訊息不可為空白")
    if len(normalized_text) > MAX_TEXT_LENGTH:
        raise LineValidationError(
            f"LINE 文字訊息長度不可超過 {MAX_TEXT_LENGTH} 字"
        )
    return normalized_text


def validate_media_message(
    message_id: str, media_type: str, file_name: Optional[str] = None
) -> None:
    if not message_id or not message_id.strip():
        raise LineValidationError("缺少 media message id")
    normalized_type = media_type.strip().lower()
    if normalized_type not in ALLOWED_MEDIA_TYPES:
        raise LineValidationError(f"不支援的媒體類型: {media_type}")
    if file_name is not None and not str(file_name).strip():
        raise LineValidationError("無效的媒體檔名")
