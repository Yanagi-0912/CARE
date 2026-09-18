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


def test_螢幕標題與網站標題是兩套寫法時仍要過門檻():
    """真實案例：TVBS 的螢幕標題「添色素‧影響智力」，網站版是「摻色素…傷智力」。

    門檻若設在 0.75，這種改寫過的標題會被擋掉——那正是長輩最常拍到的情況。
    """
    score = title_match_score("維他命添色素‧影響智力", "維他命摻色素專家：恐過敏傷智力 - TVBS新聞")
    assert score >= TvNewsArticleFinder.MATCH_THRESHOLD


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
    """實測中真正接錯的樣子：同一台、同一個健康主題，但不是那一則。"""
    search = _Search(
        [
            _Hit("不是地瓜葉！營養師推「超級食物」護心、助解毒配油吃更強 - 祝你健康", "https://health.setn.com/news/1"),
            _Hit("虐待動物｜ 標籤｜ 第1頁 - 公視新聞", "https://news.pts.org.tw/tag/1"),
        ]
    )
    assert await TvNewsArticleFinder(search).find("地瓜維生素A保護黏膜 蛤蜊含鋅助抗氧化", "三立") is None


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
async def test_認不出台別時改用高門檻_接受任何一台自己的報導():
    """線上兩次電視新聞查核都是 channel=-（畫面糊掉、台標被切掉），不能因此不查。"""
    search = _Search([_Hit("6週就見效！ 研究曝「番茄」能改善脂肪肝- 台視影音", "https://news.ttv.com.tw/a")])
    article = await TvNewsArticleFinder(search).find(HEADLINE, "")
    assert article is not None
    # 查詢字串不帶台名——台別本來就不知道
    assert search.queries == ["6週就見效! 研究曝 番茄 能改善脂肪肝"]


@pytest.mark.asyncio
async def test_認不出台別時改寫過的標題不收():
    """放寬成「任何一台」之後誤接的空間變大，門檻跟著收緊到 0.70。

    實測中公視的「調節血糖.血脂健康食品」在 0.60 會接成東森的另一篇報導。
    """
    search = _Search(
        [_Hit("血糖、血脂健康食品新制上路「三高」認證動物實驗全面退場", "https://news.ebc.net.tw/news/1")]
    )
    assert await TvNewsArticleFinder(search).find("調節血糖.血脂健康食品 刪除動物功效實驗", "") is None


@pytest.mark.asyncio
async def test_認不出台別時仍然只收電視台自己的報導():
    search = _Search([_Hit("6週就見效！ 研究曝「番茄」能改善脂肪肝 - Yahoo新聞", "https://tw.news.yahoo.com/a")])
    assert await TvNewsArticleFinder(search).find(HEADLINE, "") is None


@pytest.mark.asyncio
async def test_摘要裡的別則標題不算數():
    """整點彙整的摘要常引用別則新聞的完整標題，只比標題才擋得掉。"""
    search = _Search(
        [
            _Hit("【晨間快訊】TVBS新聞 - YouTube", "https://www.youtube.com/watch?v=y"),
        ]
    )
    assert await TvNewsArticleFinder(search).find('醫示警"不吃藥會死" 38歲洗腎男路倒不治', "TVBS") is None


@pytest.mark.asyncio
async def test_搜尋失敗不拋錯():
    """找新聞是加值，Firecrawl 429 或逾時不能讓查核卡跟著沒有。"""
    search = _Search(exc=RuntimeError("429"))
    assert await TvNewsArticleFinder(search).find(HEADLINE, "台視") is None
    assert await TvNewsArticleFinder(None).find(HEADLINE, "台視") is None
