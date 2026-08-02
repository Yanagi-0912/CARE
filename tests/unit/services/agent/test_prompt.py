from app.services.agent.prompt import SYSTEM_PROMPT


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
