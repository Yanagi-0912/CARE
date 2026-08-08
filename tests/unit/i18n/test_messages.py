import pytest

from app.core.user_language import SUPPORTED_LANGUAGES, set_request_language
from app.i18n.messages import strip_sources_section, t

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
    "location.type.title",
    "location.type.unknown",
    "location.type.pharmacy_none",
    "location.type.pharmacy_data_gap",
)

# 院所類型篩選各 key 的 placeholder 對照，供格式化測試沿用。
FACILITY_TYPE_KEY_PLACEHOLDERS = {
    "location.type.title": "type",
    "location.type.unknown": "facility_type",
    "location.type.pharmacy_none": "radius_km",
    "location.type.pharmacy_data_gap": "radius_km",
}


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


@pytest.mark.parametrize("key,placeholder", FACILITY_TYPE_KEY_PLACEHOLDERS.items())
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_t_facility_type_keys_have_placeholder_and_format_correctly(key, placeholder, language):
    template = t(key, language)
    assert "{" + placeholder + "}" in template
    formatted = template.format(**{placeholder: "PLACEHOLDER_VALUE"})
    assert "PLACEHOLDER_VALUE" in formatted


@pytest.mark.parametrize("key", FACILITY_TYPE_KEY_PLACEHOLDERS)
def test_t_facility_type_keys_en_is_not_traditional_chinese(key):
    message = t(key, "en")
    assert not any("一" <= ch <= "鿿" for ch in message)


def test_t_location_type_pharmacy_none_does_not_imply_no_pharmacy_nearby():
    """藥局資料只有 116 家，遠低於實際數量；文案不得暗示「附近真的沒有藥局」，
    且必須提供「改用藥局名稱查詢」的替代做法（Task 5 brief 的核心要求）。"""
    zh = t("location.type.pharmacy_none", "zh-TW")
    assert "藥局名稱" in zh
    assert zh != t("location.department.none", "zh-TW").replace("{department}", "藥局")

    en = t("location.type.pharmacy_none", "en")
    assert "name" in en.lower()


def test_t_location_type_pharmacy_data_gap_blames_coverage_not_geography():
    """
    「查得到藥局但最近一家在 18 公里外」的補充說明：必須把原因歸給本系統的
    收錄範圍，且提供改用名稱查詢的替代做法，語氣比照 pharmacy_none。
    不得暗示「附近真的沒有更近的藥局」。
    """
    zh = t("location.type.pharmacy_data_gap", "zh-TW")
    assert "藥局名稱" in zh
    assert "有限" in zh
    assert zh != t("location.type.pharmacy_none", "zh-TW")

    en = t("location.type.pharmacy_data_gap", "en")
    assert "limited" in en.lower()
    assert "name" in en.lower()


def test_t_agent_sources_heading_zh_tw_includes_colon():
    assert t("agent.sources_heading", "zh-TW") == "參考資料來源："


def test_t_falls_back_to_zh_tw_for_unknown_language():
    zh = t("line.fallback_ununderstood", "zh-TW")
    assert t("line.fallback_ununderstood", "fr") == zh


def test_t_falls_back_to_zh_tw_for_unknown_key():
    assert t("missing.key", "en") == t("missing.key", "zh-TW")


def test_strip_sources_section_returns_unchanged_when_no_heading():
    text = "正文段落。\n\n沒有來源區塊。"
    assert strip_sources_section(text) == text


def test_strip_sources_section_removes_heading_and_list():
    heading = t("agent.sources_heading", "zh-TW")
    text = (
        f"以下為 RAG 回應：\n\n正文內容。\n\n{heading}\n"
        "[1] 假來源: https://fake.example/a\n"
        "[2] 假來源: https://fake.example/b"
    )
    assert strip_sources_section(text) == "以下為 RAG 回應：\n\n正文內容。"


def test_strip_sources_section_strips_trailing_whitespace_before_heading():
    heading = t("agent.sources_heading", "en")
    text = f"Answer body.  \n\n{heading}\n[1] CDC: https://cdc.gov"
    assert strip_sources_section(text) == "Answer body."


def test_t_uses_request_language_when_language_is_none():
    token = set_request_language("en")
    try:
        message = t("line.fallback_ununderstood")
        assert "Sorry" in message
        assert "抱歉" not in message
    finally:
        from app.core.user_language import reset_request_language

        reset_request_language(token)
