import pytest
from unittest.mock import AsyncMock, MagicMock

from app.application.rag.retrieval.retriever import search_similar_chunks


@pytest.mark.asyncio
async def test_search_similar_chunks_delegates_to_reader():
    embedding = [0.1, 0.2, 0.3]
    expected_hits = [{"id": "a", "text": "chunk", "score": 0.9}]
    reader = MagicMock()
    reader.search_by_embedding = AsyncMock(return_value=expected_hits)

    result = await search_similar_chunks(embedding, reader=reader)

    assert result == expected_hits
    reader.search_by_embedding.assert_awaited_once_with(query_embedding=embedding)
