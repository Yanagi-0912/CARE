from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.medical_news.article_grader import ArticleJudgement
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
    """民國年仍須認得。

    **訂正 2026-09-04**：本測試原本的理由寫著「衛福部與食藥署的頁面常以民國年
    呈現」，那是未經查證的推測——實際量測 2,422 筆，民國格式 0 筆。測試保留，
    但理由改成真正的那個：gov.tw 各頁面的日期呈現不一致，上游改版時多認一種
    格式的成本是零，少認一種會讓整批文章安靜地消失。
    """
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


# ── 政策新聞稿過濾（第一道：標題黑名單，不花額度）─────────────────


@pytest.mark.asyncio
async def test_policy_announcements_are_excluded():
    """衛福部那批名為闢謠、實為政策新聞稿的內容不得成為 Tier 2 卡片。

    標題取自 `health_articles_chunks` 的真實資料（2026-09-04 量測樣本）。
    """
    docs = [
        _chunk(
            url="https://www.hpa.gov.tw/a/1",
            original_title="國健署攜手軍醫局 打造無菸健康戰力 國軍弟兄呼吸更清新",
        ),
        _chunk(
            url="https://www.hpa.gov.tw/a/2",
            original_title="試管嬰兒補助再加碼 支持不孕夫妻圓生育夢",
        ),
        _chunk(
            url="https://www.hpa.gov.tw/a/3",
            original_title="行政院院會審議通過「菸害防制法」部分條文修正草案",
        ),
        _chunk(
            url="https://www.hpa.gov.tw/a/4",
            original_title="中元節採買提物　長者掌握3要訣防跌",
        ),
    ]
    collection = _collection(docs=docs)
    service = KbDigestService(collection=collection, max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://www.hpa.gov.tw/a/4"]


# ── grader（第二道：擋黑名單擋不掉的）───────────────────────────────


class FakeArticleGrader:
    """依標題決定判定；`raises` 裡的標題直接拋例外。"""

    def __init__(self, useful_titles=None, raises=()):
        self._useful = set(useful_titles or [])
        self._raises = set(raises)
        self.calls = []

    async def judge_article(self, title, excerpt):
        self.calls.append(title)
        if title in self._raises:
            raise RuntimeError("quota exhausted")
        return ArticleJudgement(
            is_useful_for_elderly=title in self._useful, reason="測試"
        )


@pytest.mark.asyncio
async def test_grader_rejects_articles_not_useful_for_elderly():
    """標題像衛教、內容也是真衛教，但對象不是長輩——黑名單擋不到，grader 要擋。"""
    docs = [
        _chunk(url="https://a.gov.tw/kid", original_title="翻轉兒童肥胖"),
        _chunk(url="https://a.gov.tw/old", original_title="長者掌握3要訣防跌"),
    ]
    grader = FakeArticleGrader(useful_titles=["長者掌握3要訣防跌"])
    service = KbDigestService(
        collection=_collection(docs=docs),
        max_age_days=30,
        grader=grader,
        max_grade_calls=10,
    )

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://a.gov.tw/old"]


@pytest.mark.asyncio
async def test_grader_failure_excludes_the_article():
    """判定沒有發生時 fail closed——主動推播沒有人在等，沒推遠比推錯好。"""
    docs = [
        _chunk(url="https://a.gov.tw/1", original_title="判定會爆炸的一篇"),
        _chunk(url="https://a.gov.tw/2", original_title="長者掌握3要訣防跌"),
    ]
    grader = FakeArticleGrader(
        useful_titles=["長者掌握3要訣防跌"], raises=["判定會爆炸的一篇"]
    )
    service = KbDigestService(
        collection=_collection(docs=docs),
        max_age_days=30,
        grader=grader,
        max_grade_calls=10,
    )

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://a.gov.tw/2"]


@pytest.mark.asyncio
async def test_grade_budget_stops_selection_instead_of_admitting_the_rest():
    """額度用完要停止選材，不得把剩下的一律放行。

    放行等於在額度吃緊那天悄悄關掉這道防線，而那正是最需要它的時候。
    """
    docs = [
        _chunk(url=f"https://a.gov.tw/{i}", original_title=f"文章{i}")
        for i in range(6)
    ]
    grader = FakeArticleGrader(useful_titles=[])
    service = KbDigestService(
        collection=_collection(docs=docs),
        max_age_days=30,
        grader=grader,
        max_grade_calls=2,
    )

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert articles == []
    assert len(grader.calls) == 2


@pytest.mark.asyncio
async def test_blacklisted_titles_never_reach_the_grader():
    """黑名單擋在 grader 之前，才省得到額度。"""
    docs = [
        _chunk(url="https://a.gov.tw/1", original_title="國健署攜手軍醫局 打造無菸健康戰力"),
        _chunk(url="https://a.gov.tw/2", original_title="長者掌握3要訣防跌"),
    ]
    grader = FakeArticleGrader(useful_titles=["長者掌握3要訣防跌"])
    service = KbDigestService(
        collection=_collection(docs=docs),
        max_age_days=30,
        grader=grader,
        max_grade_calls=10,
    )

    await service.recent_articles("2026-09-02", limit=5)

    assert grader.calls == ["長者掌握3要訣防跌"]


@pytest.mark.asyncio
async def test_no_grader_falls_back_to_blacklist_only():
    """grader 缺席時 Tier 2 仍照常供應，只是品質退回黑名單那一層。"""
    docs = [_chunk(url="https://a.gov.tw/1", original_title="翻轉兒童肥胖")]
    service = KbDigestService(collection=_collection(docs=docs), max_age_days=30)

    articles = await service.recent_articles("2026-09-02", limit=5)

    assert [a.url for a in articles] == ["https://a.gov.tw/1"]
