"""User-facing language constants, normalization, and request-scoped ContextVar."""

from __future__ import annotations

from contextvars import ContextVar, Token

# 文字語言：介面、固定訊息、模型回答用哪一種寫。
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh-TW", "en", "id", "vi", "th", "ja")
DEFAULT_USER_LANGUAGE = "zh-TW"

# 台語只換語音：語音訊息走台語 STT、語音回覆走台語 TTS，文字一律跟 zh-TW 一樣。
# 大多數長輩讀不慣台語漢字，介面文案與知識庫也都只有華語版。
TAIWANESE_LANGUAGE = "nan-TW"
# 使用者能選、會存進資料庫的值。
USER_LANGUAGE_CHOICES: tuple[str, ...] = SUPPORTED_LANGUAGES + (TAIWANESE_LANGUAGE,)
_TEXT_LANGUAGE_BY_CHOICE = {TAIWANESE_LANGUAGE: "zh-TW"}

_request_language: ContextVar[str] = ContextVar(
    "care_request_language",
    default=DEFAULT_USER_LANGUAGE,
)

# 這一則語音實際聽出來的語言（見 speech.speech_language）。語音進來時由媒體辨識
# 設定、由 media_handler 讀走，決定這一則要用哪種語言念回去——使用者不必先到設定
# 頁把語言切成台語才能用台語問。文字訊息沒有音訊可判，維持 None。
_detected_speech_language: ContextVar[str | None] = ContextVar(
    "care_detected_speech_language",
    default=None,
)


def normalize_user_language(language: str | None) -> str:
    """使用者的選擇 → 文字語言（台語 → zh-TW；不認得的 → 預設）。"""
    language = _TEXT_LANGUAGE_BY_CHOICE.get(language, language)
    if language in SUPPORTED_LANGUAGES:
        return language
    return DEFAULT_USER_LANGUAGE


def normalize_language_choice(language: str | None) -> str:
    """使用者的選擇原樣保留（含台語）；不認得的 → 預設。"""
    if language in USER_LANGUAGE_CHOICES:
        return language
    return DEFAULT_USER_LANGUAGE


def get_request_language() -> str:
    """這個請求的文字語言。"""
    return normalize_user_language(_request_language.get())


def get_request_speech_language() -> str:
    """這個請求的語音語言：選台語的使用者是 nan-TW，其他人跟文字語言相同。"""
    return normalize_language_choice(_request_language.get())


def set_request_language(language: str) -> Token:
    return _request_language.set(normalize_language_choice(language))


def reset_request_language(token: Token) -> None:
    _request_language.reset(token)


def set_detected_speech_language(language: str | None) -> Token:
    """記下這一則語音聽出來的語言（台語或華語）。"""
    return _detected_speech_language.set(
        normalize_language_choice(language) if language else None
    )


def get_detected_speech_language() -> str | None:
    """這一則語音聽出來的語言；不是語音、或還沒判就是 None。"""
    return _detected_speech_language.get()


def reset_detected_speech_language(token: Token) -> None:
    _detected_speech_language.reset(token)
