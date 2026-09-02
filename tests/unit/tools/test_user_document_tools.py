from app.services.rag.user_document_answer_service import NO_DOCS_MESSAGE
from app.tools.user_document_tools import (
    SERVICE_UNAVAILABLE_MESSAGE,
    UNKNOWN_USER_MESSAGE,
    is_document_answer_unavailable,
)


def test_no_docs_message_is_unavailable():
    assert is_document_answer_unavailable(NO_DOCS_MESSAGE) is True


def test_service_and_user_errors_are_unavailable():
    assert is_document_answer_unavailable(SERVICE_UNAVAILABLE_MESSAGE) is True
    assert is_document_answer_unavailable(UNKNOWN_USER_MESSAGE) is True


def test_real_answer_is_available():
    assert is_document_answer_unavailable("報告指出你的血壓偏高 [1]。") is False


def test_blank_is_unavailable():
    assert is_document_answer_unavailable("") is True
    assert is_document_answer_unavailable(None) is True
