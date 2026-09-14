import pytest

from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TAIWANESE_LANGUAGE,
    USER_LANGUAGE_CHOICES,
    get_request_language,
    get_request_speech_language,
    normalize_language_choice,
    normalize_user_language,
    reset_request_language,
    set_request_language,
)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_normalize_user_language_known(language):
    assert normalize_user_language(language) == language


@pytest.mark.parametrize(
    "raw",
    ["", None, "fr", "zh-CN", "unknown"],
)
def test_normalize_user_language_unknown(raw):
    assert normalize_user_language(raw) == DEFAULT_USER_LANGUAGE


def test_get_request_language_defaults_to_zh_tw():
    token = set_request_language(DEFAULT_USER_LANGUAGE)
    try:
        assert get_request_language() == DEFAULT_USER_LANGUAGE
    finally:
        reset_request_language(token)


def test_set_and_reset_request_language():
    default_token = set_request_language(DEFAULT_USER_LANGUAGE)
    try:
        token = set_request_language("en")
        assert get_request_language() == "en"
        reset_request_language(token)
        assert get_request_language() == DEFAULT_USER_LANGUAGE
    finally:
        reset_request_language(default_token)


# 台語只換語音：文字語言一律當 zh-TW，語音語言保留台語。
def test_taiwanese_text_language_is_zh_tw():
    assert TAIWANESE_LANGUAGE in USER_LANGUAGE_CHOICES
    assert TAIWANESE_LANGUAGE not in SUPPORTED_LANGUAGES
    assert normalize_user_language(TAIWANESE_LANGUAGE) == "zh-TW"
    assert normalize_language_choice(TAIWANESE_LANGUAGE) == TAIWANESE_LANGUAGE


@pytest.mark.parametrize("raw", ["", None, "nan", "ko"])
def test_normalize_language_choice_unknown(raw):
    assert normalize_language_choice(raw) == DEFAULT_USER_LANGUAGE


def test_request_language_splits_text_and_speech_for_taiwanese():
    token = set_request_language(TAIWANESE_LANGUAGE)
    try:
        assert get_request_language() == "zh-TW"
        assert get_request_speech_language() == TAIWANESE_LANGUAGE
    finally:
        reset_request_language(token)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_speech_language_equals_text_language_for_others(language):
    token = set_request_language(language)
    try:
        assert get_request_speech_language() == get_request_language() == language
    finally:
        reset_request_language(token)
