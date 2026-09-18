"""找回電視新聞原始報導：寧可不附，也不附錯的。

門檻與命中率的實測見 `scripts/tv_news_article_eval.py`；這裡釘住的是規則本身。
"""

import pytest

from app.services.media.tv_news_lookup import (
    TvNewsArticleFinder,
    build_query,
    title_match_score,
)


class _Hit:
    def __init__(self, title: str, url: str):
        self.title, self.url = title, url


class _Search:
    def __init__(self, hits=None, exc=None):
        self.hits, self.exc = hits or [], exc
        self.queries: list[str] = []

    async def __call__(self, query, *, limit=5, include_domains=None):
        self.queries.append(query)
        if self.exc:
            raise self.exc
        return self.hits


HEADLINE = "6週就見效! 研究曝\"番茄\"能改善脂肪肝"


def test_標點與引號不影響吻合度():
    """電視標題用 .‧! 當分隔，網站標題用全形標點，同一句話不能因此比不出來。"""
    assert title_match_score(HEADLINE, "6週就見效！ 研究曝「番茄」能改善脂肪肝- 台視影音") == 1.0
    # 同一事件的別篇報導：字面重疊低
    assert title_match_score(HEADLINE, "吃番茄有助改善脂肪肝？營養師這樣說") < 0.75


def test_搜尋字串帶台名且拿掉引號():
    """引號在搜尋引擎是「完全相符」運算子，留著會把結果縮到零。"""
    assert build_query(HEADLINE, "台視") == '台視 6週就見效! 研究曝 番茄 能改善脂肪肝'


@pytest.mark.asyncio
async def test_收該台自己的網站():
    search = _Search([_Hit("6週就見效！ 研究曝「番茄」能改善脂肪肝- 台視影音", "https://news.ttv.com.tw/news/1")])
    article = await TvNewsArticleFinder(search).find(HEADLINE, "台視")
    assert article is not None and article.url.endswith("/news/1")
    assert article.is_video is False


@pytest.mark.asyncio
async def test_收該台自己上傳的影片():
    search = _Search([_Hit("吃麵食不怕肥寬麵比細麵熱量低｜華視新聞", "https://www.youtube.com/watch?v=abc")])
    article = await TvNewsArticleFinder(search).find("吃麵食不怕肥 寬麵比細麵熱量低", "華視")
    assert article is not None and article.is_video is True


@pytest.mark.asyncio
async def test_不收別台與轉載站():
    """實測裡 TVBS 那則在中時有 0.83 分的報導，那是別家記者寫的同一件事。"""
    search = _Search(
        [
            _Hit("「再不吃藥你會死」 醫一語成真38歲洗腎男路倒不治 - 中時新聞網", "https://www.chinatimes.com/a"),
            _Hit("醫示警「不吃藥會死」 38歲洗腎男路倒不治 - Yahoo新聞", "https://tw.news.yahoo.com/a"),
            _Hit("醫示警「不吃藥會死」 38歲洗腎男路倒不治 | LINE TODAY", "https://today.line.me/a"),
        ]
    )
    assert await TvNewsArticleFinder(search).find('醫示警"不吃藥會死" 38歲洗腎男路倒不治', "TVBS") is None


@pytest.mark.asyncio
async def test_不收別人上傳的同名影片():
    """YouTube 上誰都能上傳，標題沒有台名就不能當成那台的原始報導。"""
    search = _Search([_Hit("6週就見效！ 研究曝「番茄」能改善脂肪肝", "https://www.youtube.com/watch?v=x")])
    assert await TvNewsArticleFinder(search).find(HEADLINE, "台視") is None


@pytest.mark.asyncio
async def test_同一台的別則新聞被門檻擋掉():
    search = _Search([_Hit("賴總統推健康幣 健檢.癌篩「有做就有幣」 - 台視新聞", "https://news.ttv.com.tw/news/2")])
    assert await TvNewsArticleFinder(search).find("成人健檢送800點 累積滿千可兌商品.服務", "台視") is None


@pytest.mark.asyncio
async def test_文章優先於影片():
    search = _Search(
        [
            _Hit("吃麵食不怕肥寬麵比細麵熱量低｜華視新聞", "https://www.youtube.com/watch?v=abc"),
            _Hit("吃麵食不怕肥寬麵比細麵熱量低- 生活- 華視新聞網", "https://news.cts.com.tw/cts/1.html"),
        ]
    )
    article = await TvNewsArticleFinder(search).find("吃麵食不怕肥 寬麵比細麵熱量低", "華視")
    assert article is not None and article.is_video is False


@pytest.mark.asyncio
async def test_認不出台別就不搜():
    """沒有台別就只剩標題相似度，擋不住同名事件的別則報導——那不如不附。"""
    search = _Search([_Hit("6週就見效！ 研究曝「番茄」能改善脂肪肝", "https://news.ttv.com.tw/a")])
    assert await TvNewsArticleFinder(search).find(HEADLINE, "") is None
    assert search.queries == []


@pytest.mark.asyncio
async def test_搜尋失敗不拋錯():
    """找新聞是加值，Firecrawl 429 或逾時不能讓查核卡跟著沒有。"""
    search = _Search(exc=RuntimeError("429"))
    assert await TvNewsArticleFinder(search).find(HEADLINE, "台視") is None
    assert await TvNewsArticleFinder(None).find(HEADLINE, "台視") is None
