import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")
    motor_asyncio_module.AsyncIOMotorClient = object
    motor_asyncio_module.AsyncIOMotorCollection = object
    motor_module.motor_asyncio = motor_asyncio_module
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document

from app.services.rag.user_document_retriever import UserDocumentVectorRetriever


def _make_retriever(**overrides):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    kwargs = {
        "embeddings": emb,
        "mongo_uri": "mongodb://localhost",
        "db_name": "db",
        "collection_name": "user_docs",
        "index_name": "user_docs_vector",
        "vector_field": "embedding",
        "text_field": "text",
        "vector_dim": 3,
        "k": 5,
    }
    kwargs.update(overrides)
    return UserDocumentVectorRetriever(**kwargs), emb


@pytest.mark.asyncio
async def test_user_document_retriever_filter_and_text_field():
    retriever, emb = _make_retriever(vector_dim=3)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "doc1",
                "text": "飲食指南：每日熱量計算",
                "score": 0.88,
                "source_name": "diet.pdf",
                "document_id": "abc-123",
            },
            {"_id": "blank", "text": "   ", "score": 0.95},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("熱量怎麼算", line_user_id="U123456")

    assert len(docs) == 1
    assert docs[0] == Document(
        page_content="飲食指南：每日熱量計算",
        metadata={
            "id": "doc1",
            "score": 0.88,
            "source_name": "diet.pdf",
            "document_id": "abc-123",
        },
    )

    pipeline = fake_collection.aggregate.call_args.args[0]
    vector_search = pipeline[0]["$vectorSearch"]
    assert vector_search["index"] == "user_docs_vector"
    assert vector_search["queryVector"] == [0.1, 0.2, 0.3]
    assert vector_search["filter"]["line_user_id"] == {"$eq": "U123456"}
    assert "$gt" in vector_search["filter"]["expires_at"]
    assert pipeline[1]["$project"]["text"] == 1

    emb.aembed_query.assert_awaited_once_with("熱量怎麼算")


@pytest.mark.asyncio
async def test_user_document_retriever_still_filters_low_score_by_default():
    """使用者文件路徑沒有 reranker，必須保留 0.5 門檻。

    這個測試守的是「KB 路徑放寬門檻時不會連帶把這裡也放寬」。
    """
    retriever, emb = _make_retriever(vector_dim=2, text_field="text")
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "1", "text": "高分", "score": 0.9},
            {"_id": "2", "text": "低分", "score": 0.12},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("q", line_user_id="U123456")

    assert [d.page_content for d in docs] == ["高分"]
