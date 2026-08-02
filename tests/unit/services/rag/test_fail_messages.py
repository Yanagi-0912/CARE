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
