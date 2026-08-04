from app.core.user_language import reset_request_language, set_request_language
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    NO_HITS_MESSAGE,
    RagFailCode,
    is_rag_fail,
    parse_rag_fail_code,
    rag_fail,
)


def test_rag_fail_format_and_parse():
    text = rag_fail(RagFailCode.WEB_EMPTY)
    assert text.startswith("[RAG_ERR:WEB_EMPTY]")
    assert is_rag_fail(text)
    assert parse_rag_fail_code(text) == RagFailCode.WEB_EMPTY


def test_compat_aliases():
    assert NO_HITS_MESSAGE == rag_fail(RagFailCode.KB_EMPTY)
    assert NO_ANSWER_MESSAGE == rag_fail(RagFailCode.MODEL_REFUSE)
    assert not is_rag_fail("正常衛教回答")


def test_rag_fail_with_explicit_language_en():
    text = rag_fail(RagFailCode.KB_EMPTY, language="en")
    assert text.startswith("[RAG_ERR:KB_EMPTY]")
    assert "knowledge base" in text.lower()
    assert "知識庫" not in text


def test_rag_fail_uses_request_language_context():
    token = set_request_language("en")
    try:
        text = rag_fail(RagFailCode.KB_EMPTY)
        assert "knowledge base" in text.lower()
        assert "知識庫" not in text
    finally:
        reset_request_language(token)
