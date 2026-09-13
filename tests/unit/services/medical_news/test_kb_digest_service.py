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


# ── 官方優先、媒體補位 ────────────────────────────────────────────────


def _media(**overrides):
    doc = {
        "url": "https://health.udn.com/health/story/6037/1",
        "title": "年紀大吃得少血糖反而升高？醫師揭老人常見血糖NG行為",
        "source_name": "udn 元氣網",
        "published_at": "2026-09-14",
        "category": "焦點",
        "excerpt": "高齡者食量變小，血糖卻可能不降反升。",
    }
    doc.update(overrides)
    return doc


@pytest.mark.asyncio
async def test_pool_order_is_official_fresh_then_media_then_official_stock():
    """三群依序：官方新 > 媒體新 > 官方存量。

    媒體一天約 15.8 篇、官方約 2 篇，單純依日期混排時池子幾乎全是媒體——這是
    `test_blacklisted_titles_never_reach_the_grader` 那批量測（衛福部佔滿窗口
    33/50）的同一個失效形狀，換成媒體而已。
    """
    official = _collection(docs=[
        _chunk(url="https://a.gov.tw/fresh", published_at="2026-09-13"),
        _chunk(url="https://a.gov.tw/stock", published_at="2026-08-30"),
    ])
    media = _collection(docs=[_media()])
    service = KbDigestService(collection=official, max_age_days=30, media_collection=media)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert [a.url for a in pool] == [
        "https://a.gov.tw/fresh",
        "https://health.udn.com/health/story/6037/1",
        "https://a.gov.tw/stock",
    ]
    assert [a.priority for a in pool] == [0, 1, 2]


@pytest.mark.asyncio
async def test_yesterday_still_counts_as_fresh():
    """「新」是今天或昨天：ETL 在台北 08:00、推播在 09:00，昨天下午發布的
    文章今天早上才進庫，對使用者而言就是今天第一次看到。"""
    official = _collection(docs=[_chunk(published_at="2026-09-13")])
    service = KbDigestService(collection=official, max_age_days=30)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert pool[0].priority == 0


@pytest.mark.asyncio
async def test_media_disallowed_category_is_excluded():
    """分類是允許清單：不在清單內的（性愛、退休力、名人…）一律不收。"""
    media = _collection(docs=[
        _media(url="https://u/1", category="性愛"),
        _media(url="https://u/2", category="退休力"),
        _media(url="https://u/3", category=None),
        _media(url="https://u/4", category="醫療"),
    ])
    service = KbDigestService(
        collection=_collection(), max_age_days=30, media_collection=media)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert [a.url for a in pool] == ["https://u/4"]


@pytest.mark.asyncio
async def test_media_noise_titles_in_allowed_category_are_excluded():
    """允許分類裡的旅遊、兇殺、命理不得推出——都是 2026-09-13 樣本裡真實出現的標題。"""
    media = _collection(docs=[
        _media(url="https://u/1", category="養生",
               title="搭飛機別花2種冤枉錢！旅遊專家評8項航空福利"),
        _media(url="https://u/2", title="陽明山驚爆殺人棄屍 37歲男友起初還裝傻"),
        _media(url="https://u/3", title="鬼月醫院不能說的禁忌！一張病床1年送走近20人"),
        _media(url="https://u/4", title="開學遇流感升溫！單周10萬人次就醫 疾管署示警"),
    ])
    service = KbDigestService(
        collection=_collection(), max_age_days=30, media_collection=media)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert [a.url for a in pool] == ["https://u/4"]


@pytest.mark.asyncio
async def test_media_older_than_fresh_window_is_excluded():
    """媒體只當「今天的」補位，舊文不收——存量有官方那一群負責。"""
    media = _collection(docs=[
        _media(url="https://u/old", published_at="2026-09-10"),
        _media(url="https://u/new", published_at="2026-09-14"),
    ])
    service = KbDigestService(
        collection=_collection(), max_age_days=30, media_collection=media)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert [a.url for a in pool] == ["https://u/new"]


@pytest.mark.asyncio
async def test_media_query_is_bounded_by_fresh_window():
    """查詢端就只撈窗口內的，不把整個 collection 拉進記憶體。"""
    media = _collection(docs=[])
    service = KbDigestService(
        collection=_collection(), max_age_days=30, media_collection=media)

    await service.recent_articles("2026-09-14", limit=5)

    assert media.find.call_args.args[0] == {"published_at": {"$gte": "2026-09-13"}}


@pytest.mark.asyncio
async def test_media_never_goes_through_the_grader():
    """grader 的判準（對高齡讀者有沒有用）已被否決過，媒體只靠分類與標題防線。"""
    grader = MagicMock()
    grader.judge_article = AsyncMock(
        return_value=ArticleJudgement(is_useful_for_elderly=True, reason=""))
    service = KbDigestService(
        collection=_collection(), max_age_days=30,
        grader=grader, max_grade_calls=30,
        media_collection=_collection(docs=[_media()]),
    )

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert len(pool) == 1
    grader.judge_article.assert_not_called()


@pytest.mark.asyncio
async def test_without_media_collection_pool_is_official_only():
    """沒有媒體 collection 時池子只有官方——與加入媒體之前相同。"""
    service = KbDigestService(collection=_collection(docs=[_chunk()]), max_age_days=30)

    pool = await service.recent_articles("2026-09-14", limit=5)

    assert all(a.source_name != "udn 元氣網" for a in pool)
