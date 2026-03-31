import pytest

from app.infrastructure.gemini.shared.validation import MAX_INPUT_LENGTH, validate_user_input


def test_validate_accepts_normal_text():
    r = validate_user_input("  我頭痛  ")
    assert r.is_valid is True
    assert r.error_message is None


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\t"],
)
def test_validate_rejects_empty_or_whitespace(text):
    r = validate_user_input(text)
    assert r.is_valid is False
    assert r.error_message == "請輸入訊息內容，不能為空白。"


def test_validate_rejects_too_long():
    text = "a" * (MAX_INPUT_LENGTH + 1)
    r = validate_user_input(text)
    assert r.is_valid is False
    assert "過長" in (r.error_message or "")
    assert str(MAX_INPUT_LENGTH) in (r.error_message or "")


def test_validate_rejects_symbol_only():
    r = validate_user_input("!!!")
    assert r.is_valid is False
    assert r.error_message == "請輸入有意義的文字內容。"


def test_validate_accepts_digit_text():
    r = validate_user_input("123")
    assert r.is_valid is True
