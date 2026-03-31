import pytest
from unittest.mock import AsyncMock, MagicMock
import sys
import types

from app.infrastructure.gemini import GeminiResult

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

from app.application.rag.retrieval import RagNoHitsError
from app.application.rag.services import RagAnswerService


@pytest.mark.asyncio
async def test_answer_uses_hits_to_build_rag_prompt():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="RAG 回覆"))
    embed_query_provider = MagicMock()
    embed_query_provider.embed_query = AsyncMock(return_value=[0.1, 0.2])
    similar_chunk_searcher = MagicMock()
    similar_chunk_searcher.search_similar_chunks = AsyncMock(
        return_value=[
            {"id": "1", "text": "高血壓建議低鈉飲食", "score": 0.9},
            {"id": "2", "text": "規律量血壓", "score": 0.8},
        ]
    )
    vector_search_reader = MagicMock()
    svc = RagAnswerService(
        gemini_service=gemini_service,
        vector_search_reader=vector_search_reader,
        embed_query_provider=embed_query_provider,
        similar_chunk_searcher=similar_chunk_searcher,
    )
    result = await svc.answer("我有高血壓要注意什麼")

    assert result == "RAG 回覆"
    similar_chunk_searcher.search_similar_chunks.assert_awaited_once_with(
        [0.1, 0.2], vector_search_reader
    )
    prompt = gemini_service.generate_response.await_args.args[0]
    assert "高血壓建議低鈉飲食" in prompt
    assert "規律量血壓" in prompt


@pytest.mark.parametrize(
    "model_text",
    [None, ""],
    ids=["none", "empty_str"],
)
@pytest.mark.asyncio
async def test_answer_uses_default_message_when_model_returns_empty_text(model_text):
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text=model_text))
    embed_query_provider = MagicMock()
    embed_query_provider.embed_query = AsyncMock(return_value=[0.1, 0.2])
    similar_chunk_searcher = MagicMock()
    similar_chunk_searcher.search_similar_chunks = AsyncMock(
        return_value=[
            {"id": "1", "text": "高血壓建議低鈉飲食", "score": 0.9},
        ]
    )
    svc = RagAnswerService(
        gemini_service=gemini_service,
        vector_search_reader=MagicMock(),
        embed_query_provider=embed_query_provider,
        similar_chunk_searcher=similar_chunk_searcher,
    )
    result = await svc.answer("我有高血壓要注意什麼")

    assert result == "抱歉，我目前找不到相關資料，請稍後再試。"


@pytest.mark.asyncio
async def test_answer_raises_rag_no_hits_when_no_hits():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock()
    embed_query_provider = MagicMock()
    embed_query_provider.embed_query = AsyncMock(return_value=[0.1, 0.2])
    similar_chunk_searcher = MagicMock()
    similar_chunk_searcher.search_similar_chunks = AsyncMock(return_value=[])
    svc = RagAnswerService(
        gemini_service=gemini_service,
        vector_search_reader=MagicMock(),
        embed_query_provider=embed_query_provider,
        similar_chunk_searcher=similar_chunk_searcher,
    )
    with pytest.raises(RagNoHitsError):
        await svc.answer("我有高血壓要注意什麼")

    gemini_service.generate_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_raises_when_embed_query_fails():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock()
    embed_query_provider = MagicMock()
    embed_query_provider.embed_query = AsyncMock(side_effect=RuntimeError("embed failed"))
    svc = RagAnswerService(
        gemini_service=gemini_service,
        vector_search_reader=MagicMock(),
        embed_query_provider=embed_query_provider,
        similar_chunk_searcher=MagicMock(),
    )

    with pytest.raises(RuntimeError, match="embed failed"):
        await svc.answer("我有高血壓要注意什麼")


@pytest.mark.asyncio
async def test_answer_raises_when_search_fails():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock()
    embed_query_provider = MagicMock()
    embed_query_provider.embed_query = AsyncMock(return_value=[0.1, 0.2])
    similar_chunk_searcher = MagicMock()
    similar_chunk_searcher.search_similar_chunks = AsyncMock(
        side_effect=RuntimeError("search failed")
    )
    svc = RagAnswerService(
        gemini_service=gemini_service,
        vector_search_reader=MagicMock(),
        embed_query_provider=embed_query_provider,
        similar_chunk_searcher=similar_chunk_searcher,
    )
    with pytest.raises(RuntimeError, match="search failed"):
        await svc.answer("我有高血壓要注意什麼")
