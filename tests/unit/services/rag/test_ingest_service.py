import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.rag.chunking import KB_CHUNK_SIZE, KB_CHUNKER_VERSION
from app.services.rag.ingest_service import IngestResult, IngestService
from app.services.rag.web_client import ScrapedPage
from app.services.rag.whitelist import UrlPolicy

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
BLOCKED_URL = "https://www.google.com/search?q=高血壓"
PAGE_TITLE = "高血壓飲食原則"
# 約 1,200 字、句尾都有標點：會被知識庫切法切成三片以上
LONG_ARTICLE = "".join(f"第{i:03d}句衛教內容說明在這裡。" for i in range(1, 80))


def _one_vector_per_text(texts):
    return [[0.1 * (i + 1)] for i in range(len(texts))]


def _make_service(
    *,
    scrape_return="高血壓宜低鈉飲食。\n\n規律量測血壓。",
    scrape_title=PAGE_TITLE,
    final_url=None,
    embed_return=None,
    scrape_side_effect=None,
    embed_side_effect=None,
    url_policy=None,
):
    web_client = MagicMock()
    # scrape() 仍保留給 web_search_service.py 用，這裡與 scrape_page() 的
    # mock 並存，但 IngestService 實際呼叫的是 scrape_page()。
    web_client.scrape = AsyncMock(return_value=scrape_return)
    if scrape_side_effect is not None:
        web_client.scrape_page = AsyncMock(side_effect=scrape_side_effect)
    else:
        web_client.scrape_page = AsyncMock(
            return_value=ScrapedPage(
                text=scrape_return, final_url=final_url, title=scrape_title
            )
        )

    embeddings = MagicMock()
    if embed_side_effect is not None:
        embeddings.aembed_documents = AsyncMock(side_effect=embed_side_effect)
    elif embed_return is not None:
        embeddings.aembed_documents = AsyncMock(return_value=embed_return)
    else:
        # 每段輸入回一個向量：片數由切法決定，測試不必跟著切法改假資料
        embeddings.aembed_documents = AsyncMock(side_effect=_one_vector_per_text)

    collection = MagicMock()
    collection.delete_many = AsyncMock()
    collection.insert_many = AsyncMock()
    # 未指定 source_name 時會先讀既有文件來沿用名稱；預設當作庫裡還沒有這個 URL
    collection.find_one = AsyncMock(return_value=None)

    service = IngestService(
        web_client=web_client,
        embeddings=embeddings,
        collection=collection,
        text_field="text",
        vector_field="embedding",
        url_policy=url_policy or UrlPolicy(allowed_suffixes=("gov.tw",)),
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
    web_client.scrape_page.assert_not_awaited()
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
    web_client.scrape_page.assert_awaited_once_with(ALLOWED_URL)
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
    assert result.chunk_count == 1

    collection.delete_many.assert_awaited_once_with({"url": {"$in": [ALLOWED_URL]}})
    collection.insert_many.assert_awaited_once()
    docs = collection.insert_many.await_args[0][0]
    assert len(docs) == 1
    doc = docs[0]
    assert doc["text"] == scrape_text
    assert doc["embedding"] == [0.1]
    assert doc["source_name"] == "國健署"
    assert doc["url"] == ALLOWED_URL
    assert doc["original_title"] == PAGE_TITLE
    assert doc["content_hash"] == hashlib.sha256(scrape_text.encode()).hexdigest()
    assert "ingested_at" in doc
    assert "final_url" not in doc


@pytest.mark.asyncio
async def test_embedding_input_matches_etl_format():
    """向量要與 ETL 收進來的 chunk 用同一種輸入，兩批才落在可比的位置。

    CARE-data/main_pipeline.py 以 `主題：{title}\\n內容：{chunk}` 向量化。存進
    Mongo 的內文仍是不含標題的原文——標題另存 original_title，給 BM25 與精排用。
    """
    service, _, embeddings, collection = _make_service(scrape_return="高血壓宜低鈉飲食。")

    await service.ingest_url(ALLOWED_URL)

    assert embeddings.aembed_documents.await_args[0][0] == [
        f"主題：{PAGE_TITLE}\n內容：高血壓宜低鈉飲食。"
    ]
    docs = collection.insert_many.await_args[0][0]
    assert docs[0]["text"] == "高血壓宜低鈉飲食。"


@pytest.mark.asyncio
async def test_long_article_uses_kb_chunker_and_etl_chunk_fields():
    """切法、編號與版本欄位都跟 ETL 一致。

    chunk_index 從 1 起算：kb_digest_service 以 `chunk_index == 1` 取每篇文章的
    第一片，從 0 起算的話這條路收進來的文章會拿第二片當代表。
    """
    service, _, embeddings, collection = _make_service(scrape_return=LONG_ARTICLE)

    result = await service.ingest_url(ALLOWED_URL)

    docs = collection.insert_many.await_args[0][0]
    assert result.chunk_count == len(docs)
    assert len(docs) >= 3
    assert "".join(doc["text"] for doc in docs) == LONG_ARTICLE
    assert [doc["chunk_index"] for doc in docs] == list(range(1, len(docs) + 1))
    embed_inputs = embeddings.aembed_documents.await_args[0][0]
    for doc, embed_input in zip(docs, embed_inputs):
        assert len(doc["text"]) <= KB_CHUNK_SIZE
        assert doc["total_chunks"] == len(docs)
        assert doc["chunker_version"] == KB_CHUNKER_VERSION
        assert doc["original_title"] == PAGE_TITLE
        assert embed_input == f"主題：{PAGE_TITLE}\n內容：{doc['text']}"


@pytest.mark.asyncio
async def test_page_without_title_is_not_ingested():
    """與 ETL 同一條規則：沒有標題就不收。

    向量化輸入會變成「主題：」空白，BM25 的標題比對也對它無效；
    CARE-data/scraper_api.py 同樣直接跳過沒有標題的文章。庫裡既有的
    這個 URL 也不能先被刪掉。
    """
    service, _, embeddings, collection = _make_service(scrape_title="  ")

    result = await service.ingest_url(ALLOWED_URL)

    assert result.status == "empty"
    assert result.chunk_count == 0
    assert "標題" in result.message
    embeddings.aembed_documents.assert_not_awaited()
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_same_url():
    service, web_client, embeddings, collection = _make_service(
        scrape_return=LONG_ARTICLE,
    )

    first = await service.ingest_url(ALLOWED_URL)
    assert first.status == "ok"
    assert first.chunk_count >= 3

    web_client.scrape_page.return_value = ScrapedPage(
        text="只有一段。", final_url=None, title=PAGE_TITLE
    )

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


@pytest.mark.asyncio
async def test_rejects_when_final_url_leaves_whitelist():
    """抓取後最終 URL 離開白名單時整篇拒絕，且不得有任何寫入。

    事前檢查擋不住 Firecrawl 內部發生的重導向：一個合法的 gov.tw 網址
    可能被 302 到別處，內容照樣進向量庫、照樣掛著原本的網址當來源。
    """
    request_url = "https://www.hpa.gov.tw/a"
    service, web_client, embeddings, collection = _make_service(
        scrape_return="被重導向後抓到的內容。",
        final_url="https://evil.com/a",
    )

    result = await service.ingest_url(request_url)

    assert result.status == "rejected"
    assert result.url == request_url
    assert result.chunk_count == 0
    embeddings.aembed_documents.assert_not_awaited()
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepts_when_final_url_stays_in_whitelist():
    request_url = "https://www.hpa.gov.tw/a"
    service, web_client, embeddings, collection = _make_service(
        scrape_return=LONG_ARTICLE,
        final_url="https://www.hpa.gov.tw/a-new",
    )

    result = await service.ingest_url(request_url)

    assert result.status == "ok"
    assert result.url == request_url
    docs = collection.insert_many.await_args[0][0]
    assert len(docs) >= 3
    for doc in docs:
        assert doc["url"] == request_url
        assert doc["final_url"] == "https://www.hpa.gov.tw/a-new"


@pytest.mark.asyncio
async def test_missing_final_url_falls_back_to_requested():
    request_url = "https://www.hpa.gov.tw/a"
    service, web_client, embeddings, collection = _make_service(
        scrape_return=LONG_ARTICLE,
        final_url=None,
    )

    result = await service.ingest_url(request_url)

    assert result.status == "ok"
    docs = collection.insert_many.await_args[0][0]
    assert len(docs) >= 3
    for doc in docs:
        assert doc["url"] == request_url
        assert "final_url" not in doc


@pytest.mark.asyncio
async def test_delete_many_covers_pre_normalized_key():
    raw_url = "https://WWW.HPA.GOV.TW/a/?utm_source=line"
    normalized = "https://www.hpa.gov.tw/a"
    service, web_client, embeddings, collection = _make_service(
        scrape_return="高血壓宜低鈉飲食。\n\n規律量測血壓。",
        final_url=None,
    )

    result = await service.ingest_url(raw_url)

    assert result.status == "ok"
    delete_arg = collection.delete_many.await_args[0][0]
    assert set(delete_arg["url"]["$in"]) == {raw_url, normalized}
    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["url"] == normalized


@pytest.mark.asyncio
async def test_ingest_content_does_not_scrape():
    """快照入庫路徑 SHALL NOT 重新抓取——寫進庫的就是 admin 看過的那份位元組。"""
    service, web_client, embeddings, collection = _make_service()

    result = await service.ingest_content(
        ALLOWED_URL, "第一段內容。\n\n第二段內容。", title=PAGE_TITLE, source_name="國健署"
    )

    assert result.status == "ok"
    web_client.scrape_page.assert_not_awaited()
    web_client.scrape.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_content_writes_given_content_and_source_name():
    service, web_client, embeddings, collection = _make_service()

    await service.ingest_content(
        ALLOWED_URL, "第一段內容。\n\n第二段內容。", title=PAGE_TITLE, source_name="國健署"
    )

    docs = collection.insert_many.await_args[0][0]
    assert [doc["text"] for doc in docs] == ["第一段內容。\n\n第二段內容。"]
    for doc in docs:
        assert doc["source_name"] == "國健署"
        assert doc["url"] == ALLOWED_URL


@pytest.mark.asyncio
async def test_ingest_content_uses_given_title():
    service, _, embeddings, collection = _make_service()

    await service.ingest_content(ALLOWED_URL, "第一段內容。", title="國健署衛教專區")

    assert embeddings.aembed_documents.await_args[0][0] == [
        "主題：國健署衛教專區\n內容：第一段內容。"
    ]
    docs = collection.insert_many.await_args[0][0]
    assert docs[0]["original_title"] == "國健署衛教專區"


@pytest.mark.asyncio
async def test_ingest_content_without_title_is_not_ingested():
    service, _, embeddings, collection = _make_service()

    result = await service.ingest_content(ALLOWED_URL, "第一段內容。")

    assert result.status == "empty"
    embeddings.aembed_documents.assert_not_awaited()
    collection.delete_many.assert_not_awaited()
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_content_rejects_non_whitelist_url():
    service, web_client, embeddings, collection = _make_service()

    result = await service.ingest_content(BLOCKED_URL, "內容", title=PAGE_TITLE)

    assert result.status == "rejected"
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_content_marks_empty_content():
    service, web_client, embeddings, collection = _make_service()

    result = await service.ingest_content(ALLOWED_URL, "   ", title=PAGE_TITLE)

    assert result.status == "empty"
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_reuses_existing_source_name_when_not_provided():
    """「這頁已過時」是最常走的路徑，它 SHALL NOT 把既有策展來源名洗成空字串。"""
    service, web_client, embeddings, collection = _make_service()
    collection.find_one = AsyncMock(return_value={"source_name": "衛福部國健署"})

    await service.ingest_content(
        ALLOWED_URL, "第一段內容。\n\n第二段內容。", title=PAGE_TITLE
    )

    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["source_name"] == "衛福部國健署"


@pytest.mark.asyncio
async def test_reads_existing_source_name_before_deleting():
    """讀取必須排在 delete_many 之前，否則要沿用的東西已經被刪掉了。"""
    order: list[str] = []
    service, web_client, embeddings, collection = _make_service()

    async def _find_one(*args, **kwargs):
        order.append("find_one")
        return {"source_name": "衛福部國健署"}

    async def _delete_many(*args, **kwargs):
        order.append("delete_many")

    collection.find_one = AsyncMock(side_effect=_find_one)
    collection.delete_many = AsyncMock(side_effect=_delete_many)

    await service.ingest_content(
        ALLOWED_URL, "第一段內容。\n\n第二段內容。", title=PAGE_TITLE
    )

    assert order == ["find_one", "delete_many"]


@pytest.mark.asyncio
async def test_falls_back_to_given_source_name_when_existing_doc_has_none():
    service, web_client, embeddings, collection = _make_service()
    collection.find_one = AsyncMock(return_value={"source_name": ""})

    await service.ingest_content(
        ALLOWED_URL,
        "第一段內容。\n\n第二段內容。",
        title=PAGE_TITLE,
        default_source_name="高血壓防治網",
    )

    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["source_name"] == "高血壓防治網"


@pytest.mark.asyncio
async def test_existing_source_name_wins_over_page_title_default():
    """人工整理過的來源名不該被頁面 <title> 蓋掉（design.md 決策 6 的順序）。

    頁面標題以 default_source_name 傳入而非 source_name：前者是「庫裡沒有名稱
    時才用」的預設值，後者是呼叫端明確要求寫入的值（spec 的「已指定來源名稱
    時 SHALL 以指定值寫入」，也是 scripts/ingest_url.py 改名的能力所在）。
    """
    service, web_client, embeddings, collection = _make_service()
    collection.find_one = AsyncMock(return_value={"source_name": "衛福部國健署"})

    await service.ingest_content(
        ALLOWED_URL,
        "第一段內容。\n\n第二段內容。",
        title=PAGE_TITLE,
        default_source_name="衛生福利部國民健康署-最新消息",
    )

    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["source_name"] == "衛福部國健署"


@pytest.mark.asyncio
async def test_explicit_source_name_overrides_existing():
    """明確指定時以指定值寫入，否則營運再也無法為既有 URL 改名。"""
    service, web_client, embeddings, collection = _make_service()
    collection.find_one = AsyncMock(return_value={"source_name": "舊名稱"})

    await service.ingest_content(
        ALLOWED_URL,
        "第一段內容。\n\n第二段內容。",
        title=PAGE_TITLE,
        source_name="新名稱",
    )

    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["source_name"] == "新名稱"
    # 明確指定時不必去讀既有文件
    collection.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_url_also_reuses_existing_source_name():
    """沿用邏輯放在共用寫入路徑，所有呼叫端都受保護，不只知識回報那條路。"""
    service, web_client, embeddings, collection = _make_service()
    collection.find_one = AsyncMock(return_value={"source_name": "衛福部國健署"})

    await service.ingest_url(ALLOWED_URL)

    docs = collection.insert_many.await_args[0][0]
    for doc in docs:
        assert doc["source_name"] == "衛福部國健署"
