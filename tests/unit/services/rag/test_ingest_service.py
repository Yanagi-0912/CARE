import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.rag.ingest_service import IngestResult, IngestService

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
BLOCKED_URL = "https://www.google.com/search?q=高血壓"


def _make_service(
    *,
    scrape_return="高血壓宜低鈉飲食。\n\n規律量測血壓。",
    embed_return=None,
    scrape_side_effect=None,
    embed_side_effect=None,
):
    web_client = MagicMock()
    if scrape_side_effect is not None:
        web_client.scrape = AsyncMock(side_effect=scrape_side_effect)
    else:
        web_client.scrape = AsyncMock(return_value=scrape_return)

    embeddings = MagicMock()
    if embed_side_effect is not None:
        embeddings.aembed_documents = AsyncMock(side_effect=embed_side_effect)
    else:
        if embed_return is None:
            embed_return = [[0.1, 0.2], [0.3, 0.4]]
        embeddings.aembed_documents = AsyncMock(return_value=embed_return)

    collection = MagicMock()
    collection.delete_many = AsyncMock()
    collection.insert_many = AsyncMock()

    service = IngestService(
        web_client=web_client,
        embeddings=embeddings,
        collection=collection,
        text_field="text",
        vector_field="embedding",
    )
    return service, web_client, embeddings, collection


@pytest.mark.asyncio
async def test_rejects_non_whitelist_url():
    service, web_client, embeddings, collection = _make_service()

    result = await service.ingest_url(BLOCKED_URL)

    assert result == IngestResult(
        status="rejected",
        url=BLOCKED_URL,
        chunk_count=0,
        message="URL not in whitelist",
    )
    web_client.scrape.assert_not_awaited()
    embeddings.aembed_documents.assert_not_awaited()
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_scrape_no_write():
    service, web_client, embeddings, collection = _make_service(scrape_return="")

    result = await service.ingest_url(ALLOWED_URL)

    assert result.status == "empty"
    assert result.url == ALLOWED_URL
    assert result.chunk_count == 0
    web_client.scrape.assert_awaited_once_with(ALLOWED_URL)
    embeddings.aembed_documents.assert_not_awaited()
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_ingest_writes_docs():
    scrape_text = "高血壓宜低鈉飲食。\n\n規律量測血壓。"
    service, web_client, embeddings, collection = _make_service(scrape_return=scrape_text)

    result = await service.ingest_url(ALLOWED_URL, source_name="國健署")

    assert result.status == "ok"
    assert result.url == ALLOWED_URL
    assert result.chunk_count == 2

    embeddings.aembed_documents.assert_awaited_once()
    embed_args = embeddings.aembed_documents.await_args[0][0]
    assert embed_args == ["高血壓宜低鈉飲食。", "規律量測血壓。"]

    collection.delete_many.assert_awaited_once_with({"url": ALLOWED_URL})
    collection.insert_many.assert_awaited_once()
    docs = collection.insert_many.await_args[0][0]
    assert len(docs) == 2

    for i, doc in enumerate(docs):
        assert doc["text"] == embed_args[i]
        assert doc["embedding"] == [[0.1, 0.2], [0.3, 0.4]][i]
        assert doc["source_name"] == "國健署"
        assert doc["url"] == ALLOWED_URL
        assert doc["chunk_index"] == i
        assert doc["content_hash"] == hashlib.sha256(embed_args[i].encode()).hexdigest()
        assert "ingested_at" in doc


@pytest.mark.asyncio
async def test_replace_same_url():
    service, web_client, embeddings, collection = _make_service(
        scrape_return="第一段。\n\n第二段。",
        embed_return=[[0.1], [0.2]],
    )

    first = await service.ingest_url(ALLOWED_URL)
    assert first.status == "ok"
    assert first.chunk_count == 2

    web_client.scrape.return_value = "只有一段。"
    embeddings.aembed_documents.return_value = [[0.9]]

    second = await service.ingest_url(ALLOWED_URL)

    assert second.status == "ok"
    assert second.chunk_count == 1
    assert collection.delete_many.await_count == 2
    assert collection.insert_many.await_count == 2
    last_docs = collection.insert_many.await_args[0][0]
    assert len(last_docs) == 1
    assert last_docs[0]["text"] == "只有一段。"


@pytest.mark.asyncio
async def test_embed_failure_no_mongo_write():
    service, _, embeddings, collection = _make_service(
        embed_side_effect=RuntimeError("embed failed"),
    )

    result = await service.ingest_url(ALLOWED_URL)

    assert result.status == "error"
    assert result.chunk_count == 0
    assert "embed failed" in result.message
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()
