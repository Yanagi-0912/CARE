import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")
    motor_asyncio_module.AsyncIOMotorClient = object
    motor_asyncio_module.AsyncIOMotorCollection = object
    motor_module.motor_asyncio = motor_asyncio_module
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from app.infrastructure.vector_search.config import VectorSearchConfig
from app.infrastructure.vector_search.reader import MongoVectorSearchReader


def _cfg(vector_dim=3):
    return VectorSearchConfig(
        mongo_uri="mongodb://localhost",
        db_name="db",
        collection_name="coll",
        vector_index="idx",
        vector_field="embedding",
        text_field="chunk_text",
        vector_dim=vector_dim,
        default_top_k=4,
    )


@pytest.mark.asyncio
async def test_search_by_embedding_rejects_empty_query_vector():
    reader = MongoVectorSearchReader(_cfg())
    with pytest.raises(ValueError, match="cannot be empty"):
        await reader.search_by_embedding(query_embedding=[])


@pytest.mark.asyncio
async def test_search_by_embedding_rejects_wrong_dimension():
    reader = MongoVectorSearchReader(_cfg(vector_dim=3))
    with pytest.raises(ValueError, match="維度必須為 3"):
        await reader.search_by_embedding(query_embedding=[1.0, 2.0])


@pytest.mark.asyncio
async def test_search_by_embedding_maps_docs_to_hits():
    reader = MongoVectorSearchReader(_cfg(vector_dim=2))
    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": 123, "chunk_text": "A", "score": 0.9},
            {"_id": "b", "chunk_text": "B"},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    reader._collection = fake_collection

    with patch(
        "app.infrastructure.vector_search.reader.build_vector_search_pipeline",
        return_value=[{"$vectorSearch": {}}],
    ) as mock_pipeline:
        hits = await reader.search_by_embedding(query_embedding=[0.1, 0.2], k=2)

    assert hits == [
        {
            "id": "123",
            "text": "A",
            "score": 0.9,
            "source_name": None,
            "url": None,
        },
        {
            "id": "b",
            "text": "B",
            "score": None,
            "source_name": None,
            "url": None,
        },
    ]
    fake_collection.aggregate.assert_called_once_with([{"$vectorSearch": {}}])
    mock_pipeline.assert_called_once()


def test_ensure_collection_creates_motor_collection_once():
    reader = MongoVectorSearchReader(_cfg())

    fake_collection = MagicMock()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    fake_client = MagicMock()
    fake_client.__getitem__.return_value = fake_db

    with patch(
        "app.infrastructure.vector_search.reader.AsyncIOMotorClient",
        return_value=fake_client,
    ) as mock_motor:
        first = reader._ensure_collection()
        second = reader._ensure_collection()

    assert first is fake_collection
    assert second is fake_collection
    mock_motor.assert_called_once_with("mongodb://localhost")
