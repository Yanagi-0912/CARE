from app.tools.rag_tools import get_rag_answer


def test_get_rag_answer_docstring_mentions_medical_fraud():
    doc = get_rag_answer.description or get_rag_answer.__doc__ or ""
    assert "詐騙" in doc or "假藥" in doc
