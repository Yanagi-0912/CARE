"""User-facing language constants, normalization, and request-scoped ContextVar."""

from __future__ import annotations

from contextvars import ContextVar, Token

SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh-TW", "en", "id", "vi", "th", "ja")
DEFAULT_USER_LANGUAGE = "zh-TW"

_request_language: ContextVar[str] = ContextVar(
    "care_request_language",
    default=DEFAULT_USER_LANGUAGE,
)


def normalize_user_language(language: str | None) -> str:
    if language in SUPPORTED_LANGUAGES:
        return language
    return DEFAULT_USER_LANGUAGE


def get_request_language() -> str:
    return normalize_user_language(_request_language.get())


def set_request_language(language: str) -> Token:
    return _request_language.set(normalize_user_language(language))


def reset_request_language(token: Token) -> None:
    _request_language.reset(token)
