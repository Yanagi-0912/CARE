import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.rag.user_document_ingest_service import UserDocumentIngestService
from app.services.rag.user_document_store import ensure_user_docs_indexes


def _make_service(
    *,
    embed_return=None,
    embed_side_effect=None,
    ttl_seconds: int = 86400,
):
    embeddings = MagicMock()
    if embed_side_effect is not None:
        embeddings.aembed_documents = AsyncMock(side_effect=embed_side_effect)
    else:
        if embed_return is None:
            embed_return = [[0.1, 0.2], [0.3, 0.4]]
        embeddings.aembed_documents = AsyncMock(return_value=embed_return)

    collection = MagicMock()
    collection.insert_many = AsyncMock()

    service = UserDocumentIngestService(
        embeddings=embeddings,
        collection=collection,
        text_field="text",
        vector_field="embedding",
        ttl_seconds=ttl_seconds,
    )
    return service, embeddings, collection


@pytest.mark.asyncio
async def test_empty_text_no_insert():
    service, embeddings, collection = _make_service()

    result = await service.ingest_text("U123", "")

    assert result == ""
    embeddings.aembed_documents.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_line_user_id_no_insert():
    service, embeddings, collection = _make_service()

    result = await service.ingest_text("", "some text")

    assert result == ""
    embeddings.aembed_documents.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_ingest_writes_docs_with_shared_metadata():
    fixed_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    text = "第一段。\n\n第二段。"
    service, embeddings, collection = _make_service(ttl_seconds=3600)

    with patch(
        "app.services.rag.user_document_ingest_service.datetime"
    ) as mock_datetime:
        mock_datetime.now.return_value = fixed_now
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        result = await service.ingest_text(
            "U123",
            text,
            source_name="report.pdf",
            media_type="file",
        )

    assert result
    embeddings.aembed_documents.assert_awaited_once()
    embed_args = embeddings.aembed_documents.await_args[0][0]
    assert embed_args == ["第一段。", "第二段。"]

    collection.insert_many.assert_awaited_once()
    docs = collection.insert_many.await_args[0][0]
    assert len(docs) == 2

    document_ids = {doc["document_id"] for doc in docs}
    assert len(document_ids) == 1
    assert result == docs[0]["document_id"]

    expected_expires = fixed_now + timedelta(seconds=3600)
    for i, doc in enumerate(docs):
        assert doc["text"] == embed_args[i]
        assert doc["embedding"] == [[0.1, 0.2], [0.3, 0.4]][i]
        assert doc["line_user_id"] == "U123"
        assert doc["source_name"] == "report.pdf"
        assert doc["media_type"] == "file"
        assert doc["chunk_index"] == i
        assert doc["content_hash"] == hashlib.sha256(embed_args[i].encode()).hexdigest()
        assert doc["ingested_at"] == fixed_now.isoformat()
        assert doc["expires_at"] == expected_expires
        assert isinstance(doc["expires_at"], datetime)


@pytest.mark.asyncio
async def test_embed_failure_raises():
    service, embeddings, collection = _make_service(
        embed_side_effect=RuntimeError("embed failed"),
    )

    with pytest.raises(RuntimeError, match="embed failed"):
        await service.ingest_text("U123", "some text")

    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_user_docs_indexes_creates_ttl():
    collection = MagicMock()
    collection.create_index = AsyncMock()

    await ensure_user_docs_indexes(collection)

    collection.create_index.assert_awaited_once_with(
        [("expires_at", 1)],
        name="user_docs_expires_at_ttl",
        expireAfterSeconds=0,
    )
