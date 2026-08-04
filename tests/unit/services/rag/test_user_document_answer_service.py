import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")

    class _DummyMotorClient:
        pass

    class _DummyMotorCollection:
        pass

    class _DummyMotorDatabase:
        pass

    motor_asyncio_module.AsyncIOMotorClient = _DummyMotorClient
    motor_asyncio_module.AsyncIOMotorCollection = _DummyMotorCollection
    motor_asyncio_module.AsyncIOMotorDatabase = _DummyMotorDatabase
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.services.rag.user_document_answer_service import (
    NO_DOCS_MESSAGE,
    UserDocumentAnswerService,
)


def _make_service(*, docs, answer_content="上傳文件回覆"):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )

    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=docs)

    return (
        UserDocumentAnswerService(
            gemini_service=gemini_service,
            retriever=retriever,
        ),
        gemini_service,
        retriever,
    )


@pytest.mark.asyncio
async def test_answer_returns_friendly_message_when_no_docs():
    svc, gemini_service, retriever = _make_service(docs=[])

    result = await svc.answer("U123", "這份報告寫什麼")

    assert result == NO_DOCS_MESSAGE
    retriever.ainvoke.assert_awaited_once_with(
        "這份報告寫什麼", line_user_id="U123"
    )
    gemini_service.chat_model.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_answer_generates_from_retrieved_docs():
    docs = [
        Document(
            page_content="檢查結果：血壓正常",
            metadata={
                "id": "1",
                "score": 0.9,
                "source_name": "健檢報告.pdf",
                "document_id": "doc-abc",
            },
        ),
    ]
    svc, gemini_service, retriever = _make_service(docs=docs)

    result = await svc.answer("U123", "我的血壓如何")

    assert result == "上傳文件回覆"
    retriever.ainvoke.assert_awaited_once_with("我的血壓如何", line_user_id="U123")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    assert "健檢報告.pdf" in prompt
    assert "檢查結果：血壓正常" in prompt
