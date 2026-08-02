from app.i18n.messages import t
from app.services.agent.prompt import SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_requires_rag_for_health_and_medical_fraud():
    assert "get_rag_answer" in SYSTEM_PROMPT
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
    assert "我有孕痛" in SYSTEM_PROMPT
    assert "附近有哪些醫院" in SYSTEM_PROMPT
    assert "禁止呼叫 `get_rag_answer`" in SYSTEM_PROMPT or "禁止 `get_rag_answer`" in SYSTEM_PROMPT
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


def test_build_system_prompt_unknown_language_falls_back_to_zh_tw():
    prompt = build_system_prompt("fr")
    assert "繁體中文" in prompt
    assert t("agent.rag_prefix", "zh-TW") in prompt
