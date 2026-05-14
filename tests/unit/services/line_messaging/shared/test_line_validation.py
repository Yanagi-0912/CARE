import pytest

from app.services.line_messaging.shared.errors import LineValidationError
from app.services.line_messaging.shared.validation import (
    validate_media_message,
    validate_reply_context,
    validate_text_message,
)


def test_validate_reply_context_success() -> None:
    validate_reply_context("reply_token", "U123")


@pytest.mark.parametrize(
    "reply_token,user_id,error_msg",
    [
        ("", "U123", "reply_token"),
        ("reply_token", None, "user_id"),
    ],
)
def test_validate_reply_context_failures(
    reply_token: str, user_id: str | None, error_msg: str
) -> None:
    with pytest.raises(LineValidationError, match=error_msg):
        validate_reply_context(reply_token, user_id)


def test_validate_text_message_normalizes_spaces() -> None:
    assert validate_text_message(" 你好 ") == "你好"


def test_validate_text_message_rejects_blank() -> None:
    with pytest.raises(LineValidationError, match="不可為空白"):
        validate_text_message("   ")


def test_validate_media_message_success() -> None:
    validate_media_message("M123", "image", "a.jpg")


def test_validate_media_message_rejects_unsupported_type() -> None:
    with pytest.raises(LineValidationError, match="不支援的媒體類型"):
        validate_media_message("M123", "unknown")
