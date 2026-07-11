import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# 測試環境可能未安裝 motor，先提供最小 stub 避免 import 失敗
if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")

    class _DummyMotorClient:
        pass

    class _DummyMotorCollection:
        pass

    motor_asyncio_module.AsyncIOMotorClient = _DummyMotorClient
    motor_asyncio_module.AsyncIOMotorCollection = _DummyMotorCollection
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.services.rag import (
    CITE_TOP_K,
    NO_HITS_MESSAGE,
    RETRIEVAL_TOP_K,
    RagAnswerService,
)


def _make_service(*, docs, answer_content="RAG 回覆"):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )

    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=docs)

    return (
        RagAnswerService(gemini_service=gemini_service, retriever=retriever),
        gemini_service,
        retriever,
    )


@pytest.mark.asyncio
async def test_answer_uses_docs_to_build_rag_prompt():
    docs = [
        Document(
            page_content="高血壓建議低鈉飲食",
            metadata={
                "id": "1",
                "score": 0.9,
                "source_name": "衛福部闢謠網站",
                "url": "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
            },
        ),
        Document(
            page_content="規律量血壓",
            metadata={
                "id": "2",
                "score": 0.8,
                "source_name": "衛福部闢謠網站",
                "url": "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
            },
        ),
    ]
    svc, gemini_service, retriever = _make_service(docs=docs)
    result = await svc.answer("我有高血壓要注意什麼")

    assert "RAG 回覆" in result
    assert "資料來源：" in result
    assert "衛福部闢謠網站" in result
    retriever.ainvoke.assert_awaited_once_with("我有高血壓要注意什麼")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    assert "高血壓建議低鈉飲食" in prompt
    assert "規律量血壓" in prompt


@pytest.mark.asyncio
async def test_answer_puts_all_docs_in_prompt_but_cites_top_3_only():
    docs = [
        Document(
            page_content=f"知識內容 {i}",
            metadata={
                "id": str(i),
                "score": 1.0 - i * 0.05,
                "source_name": f"來源 {i}",
                "url": f"https://example.com/{i}",
            },
        )
        for i in range(1, RETRIEVAL_TOP_K + 1)
    ]
    svc, gemini_service, _retriever = _make_service(docs=docs)
    result = await svc.answer("測試問題")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    for i in range(1, RETRIEVAL_TOP_K + 1):
        assert f"{i}. 知識內容 {i}" in prompt

    for i in range(1, CITE_TOP_K + 1):
        assert f"[{i}] 來源 {i}：https://example.com/{i}" in result
    assert "來源 4" not in result
    assert "https://example.com/4" not in result


@pytest.mark.parametrize("model_text", [""], ids=["empty_str"])
@pytest.mark.asyncio
async def test_answer_uses_default_message_when_model_returns_empty_text(model_text):
    docs = [
        Document(
            page_content="高血壓建議低鈉飲食",
            metadata={"id": "1", "score": 0.9, "source_name": None, "url": None},
        )
    ]
    svc, _gemini, _retriever = _make_service(docs=docs, answer_content=model_text)
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == "抱歉，我目前找不到相關資料，請稍後再試。"


@pytest.mark.asyncio
async def test_answer_returns_message_when_no_docs():
    svc, gemini_service, _retriever = _make_service(docs=[])
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == NO_HITS_MESSAGE
    gemini_service.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_raises_when_retriever_fails():
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock()
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(side_effect=RuntimeError("search failed"))

    svc = RagAnswerService(gemini_service=gemini_service, retriever=retriever)
    with pytest.raises(RuntimeError, match="search failed"):
        await svc.answer("我有高血壓要注意什麼")
