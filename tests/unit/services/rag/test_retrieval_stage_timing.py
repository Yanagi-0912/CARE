"""檢索的兩段耗時要分開記錄：query embedding 與 Atlas 向量搜尋。

原本只有 rag_retrieve 一個總數，分不出慢在 Gemini embedding 還是 Atlas。
記憶檢索打算在 guardrail（實測約 1.3 秒）期間並行做完，這個前提成不成立，
取決於這兩段各自的耗時，所以要從正式流量的 `stage=` 日誌量出來。
"""

import logging
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

from app.services.rag.retriever import MongoAtlasVectorRetriever
from app.services.rag.user_document_retriever import UserDocumentVectorRetriever

_RETRIEVER_KWARGS = {
    "mongo_uri": "mongodb://localhost",
    "db_name": "db",
    "collection_name": "coll",
    "index_name": "idx",
    "vector_field": "embedding",
    "text_field": "text",
    "vector_dim": 3,
    "k": 4,
}


def _embeddings(**mock_kwargs):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(**(mock_kwargs or {"return_value": [0.1, 0.2, 0.3]}))
    return emb


def _empty_collection():
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    collection = MagicMock()
    collection.aggregate.return_value = cursor
    return collection


def _stage_lines(caplog, stage: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith(f"stage={stage} ")
    ]


@pytest.mark.asyncio
async def test_kb_retriever_logs_embedding_and_vector_search_separately(caplog):
    retriever = MongoAtlasVectorRetriever(embeddings=_embeddings(), **_RETRIEVER_KWARGS)
    retriever._collection = _empty_collection()

    with caplog.at_level(logging.INFO):
        await retriever.ainvoke("高血壓")

    [embed_line] = _stage_lines(caplog, "embed_query")
    [search_line] = _stage_lines(caplog, "vector_search")
    assert "retriever=kb" in embed_line and "ms=" in embed_line
    assert "retriever=kb" in search_line and "ms=" in search_line


@pytest.mark.asyncio
async def test_user_document_retriever_logs_embedding_and_vector_search_separately(caplog):
    retriever = UserDocumentVectorRetriever(embeddings=_embeddings(), **_RETRIEVER_KWARGS)
    retriever._collection = _empty_collection()

    with caplog.at_level(logging.INFO):
        await retriever.ainvoke("熱量怎麼算", line_user_id="U123")

    [embed_line] = _stage_lines(caplog, "embed_query")
    [search_line] = _stage_lines(caplog, "vector_search")
    assert "retriever=user_docs" in embed_line
    assert "retriever=user_docs" in search_line


# 實測出現過 94 秒才回來、最後靜靜回 0 筆的檢索，疑似 embedding 被限流。
# 最該量的正是這種失敗路徑，所以 embedding 拋例外時也要留下耗時。
@pytest.mark.asyncio
async def test_embedding_failure_still_logs_its_duration(caplog):
    retriever = MongoAtlasVectorRetriever(
        embeddings=_embeddings(side_effect=TimeoutError("embedding timed out")),
        **_RETRIEVER_KWARGS,
    )
    retriever._collection = _empty_collection()

    with caplog.at_level(logging.INFO):
        with pytest.raises(TimeoutError):
            await retriever.ainvoke("高血壓")

    [embed_line] = _stage_lines(caplog, "embed_query")
    assert "retriever=kb" in embed_line
