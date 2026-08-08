"""使用者字級設定的常數、正規化與 request-scoped ContextVar。

與 user_language 採同一套模式：Webhook 進來時由 handler 設定，
LangChain tool 這類拿不到使用者參數的地方則直接讀 ContextVar。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

SUPPORTED_FONT_SIZES: tuple[str, ...] = ("normal", "large", "xlarge")
DEFAULT_USER_FONT_SIZE = "large"  # 與 UserSettings.font_size 預設值一致

_request_font_size: ContextVar[str] = ContextVar(
    "care_request_font_size",
    default=DEFAULT_USER_FONT_SIZE,
)


def normalize_user_font_size(font_size: str | None) -> str:
    if font_size in SUPPORTED_FONT_SIZES:
        return font_size
    return DEFAULT_USER_FONT_SIZE


def get_request_font_size() -> str:
    return normalize_user_font_size(_request_font_size.get())


def set_request_font_size(font_size: str) -> Token:
    return _request_font_size.set(normalize_user_font_size(font_size))


def reset_request_font_size(token: Token) -> None:
    _request_font_size.reset(token)
