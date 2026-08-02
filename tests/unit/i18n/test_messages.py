import pytest

from app.core.user_language import SUPPORTED_LANGUAGES, set_request_language
from app.i18n.messages import t

REQUIRED_KEYS = (
    "rag.fail.KB_EMPTY",
    "rag.fail.WEB_EMPTY",
    "rag.fail.WEB_ERROR",
    "rag.fail.MODEL_REFUSE",
    "agent.rag_prefix",
    "agent.sources_heading",
    "rag.web_source_label",
    "rag.web_answer_prefix",
    "rag.generate_fallback",
    "line.fallback_ununderstood",
    "line.fallback_process_error",
    "location.share_prompt",
    "location.share_qr_label",
    "location.no_facility",
    "meds.recorded",
    "meds.already_recorded",
    "voice.enabled",
    "voice.disabled",
    "voice.need_login",
)


@pytest.mark.parametrize("key", REQUIRED_KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_t_returns_non_empty_for_all_supported_languages(key, language):
    message = t(key, language)
    assert message
    assert message.strip()


def test_t_rag_fail_kb_empty_en_is_not_traditional_chinese():
    message = t("rag.fail.KB_EMPTY", "en")
    assert "knowledge base" in message.lower()
    assert "知識庫" not in message


def test_t_agent_sources_heading_zh_tw_includes_colon():
    assert t("agent.sources_heading", "zh-TW") == "參考資料來源："


def test_t_falls_back_to_zh_tw_for_unknown_language():
    zh = t("line.fallback_ununderstood", "zh-TW")
    assert t("line.fallback_ununderstood", "fr") == zh


def test_t_falls_back_to_zh_tw_for_unknown_key():
    assert t("missing.key", "en") == t("missing.key", "zh-TW")


def test_t_uses_request_language_when_language_is_none():
    token = set_request_language("en")
    try:
        message = t("line.fallback_ununderstood")
        assert "Sorry" in message
        assert "抱歉" not in message
    finally:
        from app.core.user_language import reset_request_language

        reset_request_language(token)
