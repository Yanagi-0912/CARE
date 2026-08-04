from contextvars import Token
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.rag.user_document_answer_service import NO_DOCS_MESSAGE
from app.tools import user_document_tools
from app.tools.knowledge_report_tools import reset_line_user_id, set_line_user_id
from app.tools.user_document_tools import (
    answer_from_uploaded_document,
    configure_user_document_tool,
)


@pytest.fixture(autouse=True)
def reset_tool_state():
    configure_user_document_tool(None)
    yield
    configure_user_document_tool(None)


@pytest.mark.asyncio
async def test_answer_from_uploaded_document_without_line_user_id():
    service = MagicMock()
    service.answer = AsyncMock()
    configure_user_document_tool(service)

    result = await answer_from_uploaded_document.ainvoke({"query": "報告內容是什麼"})
    assert "無法取得使用者身分" in result
    service.answer.assert_not_called()


@pytest.mark.asyncio
async def test_answer_from_uploaded_document_without_service():
    token: Token = set_line_user_id("U123")
    try:
        result = await answer_from_uploaded_document.ainvoke({"query": "報告內容是什麼"})
        assert "未初始化" in result
    finally:
        reset_line_user_id(token)


@pytest.mark.asyncio
async def test_answer_from_uploaded_document_no_docs():
    service = MagicMock()
    service.answer = AsyncMock(return_value=NO_DOCS_MESSAGE)
    configure_user_document_tool(service)

    token: Token = set_line_user_id("U123")
    try:
        result = await answer_from_uploaded_document.ainvoke({"query": "報告內容是什麼"})
        assert result == NO_DOCS_MESSAGE
        service.answer.assert_awaited_once_with("U123", "報告內容是什麼")
    finally:
        reset_line_user_id(token)


@pytest.mark.asyncio
async def test_answer_from_uploaded_document_with_docs():
    service = MagicMock()
    service.answer = AsyncMock(return_value="依上傳文件，血壓正常。")
    configure_user_document_tool(service)

    token: Token = set_line_user_id("U456")
    try:
        with patch.object(
            user_document_tools, "get_line_user_id", return_value="U456"
        ):
            result = await answer_from_uploaded_document.ainvoke(
                {"query": "我的血壓如何"}
            )
        assert result == "依上傳文件，血壓正常。"
        service.answer.assert_awaited_once_with("U456", "我的血壓如何")
    finally:
        reset_line_user_id(token)
