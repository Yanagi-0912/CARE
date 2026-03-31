import re
import logging
from app.infrastructure.gemini.shared.types import ValidationResult

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 5000
MIN_INPUT_LENGTH = 1

# 純符號 / 純標點（不含任何文字或數字）視為無效輸入
_SYMBOL_ONLY_PATTERN = re.compile(r"^[\s\W]+$", re.UNICODE)


def validate_user_input(text: str) -> ValidationResult:
    # 使用者輸入不能為空
    if not text or not text.strip():
        logger.warning("Validation failed: empty input")
        return ValidationResult(
            is_valid=False, error_message="請輸入訊息內容，不能為空白。"
        )

    stripped = text.strip()

    # 長度下限
    if len(stripped) < MIN_INPUT_LENGTH:
        logger.warning(f"Validation failed: input too short ({len(stripped)} chars)")
        return ValidationResult(
            is_valid=False, error_message="訊息內容太短，請提供更多資訊。"
        )

    # 長度上限
    if len(stripped) > MAX_INPUT_LENGTH:
        logger.warning(f"Validation failed: input too long ({len(stripped)} chars)")
        return ValidationResult(
            is_valid=False,
            error_message=f"訊息內容過長（上限 {MAX_INPUT_LENGTH} 字），請精簡後再傳送。",
        )

    # 純符號 / 純標點
    if _SYMBOL_ONLY_PATTERN.match(stripped):
        logger.warning("Validation failed: symbol-only input")
        return ValidationResult(
            is_valid=False, error_message="請輸入有意義的文字內容。"
        )

    return ValidationResult(is_valid=True)
