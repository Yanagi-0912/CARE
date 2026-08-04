import pytest

from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    SUPPORTED_LANGUAGES,
    get_request_language,
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
