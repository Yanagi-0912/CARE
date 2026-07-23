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

from app.services.rag.retriever import MongoAtlasVectorRetriever


def _make_retriever(**overrides):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    kwargs = {
        "embeddings": emb,
        "mongo_uri": "mongodb://localhost",
        "db_name": "db",
        "collection_name": "coll",
        "index_name": "idx",
        "vector_field": "embedding",
        "text_field": "chunk_text",
        "vector_dim": 3,
        "k": 4,
    }
    kwargs.update(overrides)
    return MongoAtlasVectorRetriever(**kwargs), emb


@pytest.mark.asyncio
async def test_retriever_rejects_empty_embedding():
    retriever, emb = _make_retriever()
    emb.aembed_query = AsyncMock(return_value=[])
    with pytest.raises(ValueError, match="cannot be empty"):
        await retriever.ainvoke("高血壓")


@pytest.mark.asyncio
async def test_retriever_rejects_wrong_dimension():
    retriever, emb = _make_retriever(vector_dim=3)
    emb.aembed_query = AsyncMock(return_value=[1.0, 2.0])
    with pytest.raises(ValueError, match="維度必須為 3"):
        await retriever.ainvoke("高血壓")


@pytest.mark.asyncio
async def test_retriever_returns_documents():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": 123,
                "chunk_text": "A",
                "score": 0.9,
                "source_name": "來源A",
                "url": "https://a.example",
            },
            {"_id": "b", "chunk_text": "B"},
            {"_id": "c", "chunk_text": "   "},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")

    assert len(docs) == 2
    assert docs[0] == Document(
        page_content="A",
        metadata={
            "id": "123",
            "score": 0.9,
            "source_name": "來源A",
            "url": "https://a.example",
        },
    )
    assert docs[1].page_content == "B"
    emb.aembed_query.assert_awaited_once_with("高血壓")

    pipeline = fake_collection.aggregate.call_args.args[0]
    assert pipeline[0]["$vectorSearch"]["index"] == "idx"
    assert pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]
    assert pipeline[0]["$vectorSearch"]["limit"] == 4
    assert pipeline[0]["$vectorSearch"]["numCandidates"] == 120
    assert pipeline[1]["$project"]["chunk_text"] == 1


def test_ensure_collection_creates_motor_collection_once():
    retriever, _emb = _make_retriever()

    fake_collection = MagicMock()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    fake_client = MagicMock()
    fake_client.__getitem__.return_value = fake_db

    from unittest.mock import patch

    with patch(
        "app.services.rag.retriever.AsyncIOMotorClient",
        return_value=fake_client,
    ) as mock_motor:
        first = retriever._ensure_collection()
        second = retriever._ensure_collection()

    assert first is fake_collection
    assert second is fake_collection
    mock_motor.assert_called_once_with("mongodb://localhost")
