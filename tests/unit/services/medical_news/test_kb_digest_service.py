from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.medical_news.kb_digest_service import KbDigestService


def _collection(docs=None) -> MagicMock:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=list(docs or []))
    collection.find = MagicMock(return_value=cursor)
    return collection


def _chunk(**overrides):
    doc = {
        "source_name": "衛生福利部",
        "url": "https://www.hpa.gov.tw/Pages/Detail.aspx?pid=1",
        "original_title": "天氣熱如何補水",
        "chunk_content": "夏天應注意水分補充，每日至少……",
        "chunk_index": 1,
        "published_at": "2026-08-30",
    }
    doc.update(overrides)
    return doc


@pytest.mark.asyncio
async def test_queries_only_first_chunk_of_each_article():
    """一篇文章多個 chunk，只查 chunk_index=1。

    這同時解決兩件事：文章天然只回一筆（不必撈全部再收斂），而摘錄天然來自
    第一段——中段的 chunk 單獨呈現常常是半句話。
    """
    collection = _collection()
    service = KbDigestService(collection=collection, max_age_days=30)

    await service.recent_articles("2026-09-02", limit=3)

    query = collection.find.call_args.args[0]
    assert query["chunk_index"] == 1


@pytest.mark.asyncio
async def test_articles_without_url_are_excluded():
    """無網址的來源（食藥署 DataAction feed）不得成為消息卡。

    消息卡必須有可點的來源連結，分享給家人的卡片尤其——那是收件人唯一能
    自行查證的東西。
    """
    collection = _collection(
        docs=[_chunk(url=None), _chunk(url=""), _chunk(url="https://a.gov.tw/1")]
    )
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://a.gov.tw/1"]


@pytest.mark.asyncio
async def test_duplicate_urls_are_collapsed():
    """重切片留下的舊文件可能讓同一 url 出現多筆 chunk_index=1。"""
    collection = _collection(docs=[_chunk(), _chunk()])
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert len(articles) == 1


@pytest.mark.asyncio
async def test_excerpt_comes_from_chunk_content():
    collection = _collection(docs=[_chunk(chunk_content="夏天應注意水分補充。")])
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert articles[0].excerpt.startswith("夏天應注意水分補充")


@pytest.mark.asyncio
async def test_articles_older_than_max_age_excluded():
    collection = _collection(
        docs=[
            _chunk(url="https://a.gov.tw/new", published_at="2026-08-30"),
            _chunk(url="https://a.gov.tw/old", published_at="2026-01-01"),
        ]
    )
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://a.gov.tw/new"]


@pytest.mark.asyncio
async def test_roc_dates_are_accepted():
    """衛福部與食藥署的頁面常以民國年呈現。"""
    collection = _collection(docs=[_chunk(published_at="115-09-01")])
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert len(articles) == 1


@pytest.mark.asyncio
async def test_articles_without_parsable_date_excluded():
    collection = _collection(docs=[_chunk(published_at=None)])
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert articles == []


@pytest.mark.asyncio
async def test_results_sorted_by_published_at_desc():
    collection = _collection()
    service = KbDigestService(collection=collection, max_age_days=30)

    await service.recent_articles("2026-09-02", limit=3)

    collection.find.return_value.sort.assert_called_once_with("published_at", -1)


@pytest.mark.asyncio
async def test_limit_is_respected():
    docs = [
        _chunk(url=f"https://a.gov.tw/{i}", published_at="2026-08-30")
        for i in range(10)
    ]
    collection = _collection(docs=docs)
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=3)

    assert len(articles) == 3


@pytest.mark.asyncio
async def test_articles_without_title_excluded():
    """標題是卡片上唯一必然顯示的東西，沒有標題的文章渲染出來是空白卡。"""
    collection = _collection(docs=[_chunk(original_title="")])
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert articles == []
