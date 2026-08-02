from app.services.agent.prompt import SYSTEM_PROMPT


def test_system_prompt_requires_rag_for_health_and_medical_fraud():
    assert "get_rag_answer" in SYSTEM_PROMPT
    assert "詐騙" in SYSTEM_PROMPT or "識詐" in SYSTEM_PROMPT
    assert "165" in SYSTEM_PROMPT
    # 必須查庫（硬規則訊號）
    assert "必須" in SYSTEM_PROMPT and "get_rag_answer" in SYSTEM_PROMPT
    assert "執法" in SYSTEM_PROMPT
    assert "匯款" in SYSTEM_PROMPT
