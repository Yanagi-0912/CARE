from app.i18n.messages import t
from app.services.agent.prompt import SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_requires_rag_for_health_and_medical_fraud():
    assert "get_rag_answer" in SYSTEM_PROMPT
    assert "answer_from_uploaded_document" in SYSTEM_PROMPT
    assert "詐騙" in SYSTEM_PROMPT or "識詐" in SYSTEM_PROMPT
    assert "165" in SYSTEM_PROMPT
    assert "必須" in SYSTEM_PROMPT and "get_rag_answer" in SYSTEM_PROMPT
    assert "執法" in SYSTEM_PROMPT
    assert "匯款" in SYSTEM_PROMPT


def test_system_prompt_bans_markdown_and_routes_tools():
    assert "禁用 Markdown" in SYSTEM_PROMPT
    assert "[文字](網址)" in SYSTEM_PROMPT
    assert "request_location_quick_reply" in SYSTEM_PROMPT
    assert "lookup_medical_facility" in SYSTEM_PROMPT
    assert "open_official_site" in SYSTEM_PROMPT
    assert "打開官網" in SYSTEM_PROMPT
    assert "我有孕痛" in SYSTEM_PROMPT
    assert "附近有哪些醫院" in SYSTEM_PROMPT
    assert "禁止呼叫 `get_rag_answer`" in SYSTEM_PROMPT or "禁止 `get_rag_answer`" in SYSTEM_PROMPT
    assert "answer_from_uploaded_document" in SYSTEM_PROMPT
    assert "我剛上傳的報告" in SYSTEM_PROMPT
    assert "[RAG_ERR:" in SYSTEM_PROMPT
    assert "WEB_EMPTY" in SYSTEM_PROMPT


def test_build_system_prompt_en_requires_english_not_traditional_chinese():
    prompt = build_system_prompt("en")
    assert "必須只使用繁體中文" not in prompt
    assert "English" in prompt
    assert t("agent.rag_prefix", "en") in prompt
    assert t("agent.sources_heading", "en") in prompt


def test_build_system_prompt_zh_tw_requires_traditional_chinese():
    prompt = build_system_prompt("zh-TW")
    assert "繁體中文" in prompt
    assert t("agent.rag_prefix", "zh-TW") in prompt
    assert t("agent.sources_heading", "zh-TW") in prompt


def test_system_prompt_rule_8_preserves_sources_when_present():
    assert "參考來源網址" in SYSTEM_PROMPT
    assert "完整保留" in SYSTEM_PROMPT
    assert "不得修改網址" in SYSTEM_PROMPT


def test_system_prompt_rule_8_forbids_fabricated_sources_when_absent():
    assert "嚴禁" in SYSTEM_PROMPT
    assert "不含" in SYSTEM_PROMPT
    assert "自行新增" in SYSTEM_PROMPT or "捏造" in SYSTEM_PROMPT


def test_build_system_prompt_unknown_language_falls_back_to_zh_tw():
    prompt = build_system_prompt("fr")
    assert "繁體中文" in prompt
    assert t("agent.rag_prefix", "zh-TW") in prompt


def test_system_prompt_rule_9_lists_verify_claim_among_flex_verbatim_tools():
    """次要 finding 3：規則 9 的 Flex 原樣輸出工具清單過去只列了
    find_nearby_hospitals／find_nearby_facilities_by_department／
    lookup_medical_facility／open_official_site，沒有 verify_claim——即使
    agent.py 的 medical_tool_names 機制實際上已經涵蓋 verify_claim（見
    test_agent.py 的回歸測試），系統提示的文字說明本身仍應完整列出，
    避免日後有人以「規則 9 已涵蓋」為由精簡掉那段機制。"""
    assert "verify_claim" in SYSTEM_PROMPT
    assert "Flex Message" in SYSTEM_PROMPT
