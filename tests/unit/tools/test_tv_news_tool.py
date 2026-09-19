"""電視新聞查核工具：判定卡多一顆新聞原文，而且找新聞不能拖慢或拖垮查核。"""

import asyncio
import json

import pytest

import app.tools.claim_tools as claim_tools
import app.tools.tv_news_tools as tv_news_tools
from app.services.media.tv_news_lookup import TvNewsArticle
from app.services.rag.claim_verification.service import VerificationResult

HEADLINE = "維他命添色素‧影響智力"


def _result(matched: bool = True) -> VerificationResult:
    return VerificationResult(
        user_question=HEADLINE,
        verdict="錯誤",
        reasoning="查核報告指出這個說法錯誤。",
        source_title="查核報告",
        source_url="https://tfc-taiwan.org.tw/articles/1",
        matched=matched,
        related_info="",
        verdict_slug="incorrect",
    )


class _Service:
    def __init__(self, result=None, delay: float = 0.0):
        self._result = result or _result()
        self._delay = delay
        self.queries: list[str] = []

    async def verify(self, query: str) -> VerificationResult:
        self.queries.append(query)
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._result


class _Finder:
    def __init__(self, article=None, exc=None, delay: float = 0.0):
        self.article, self.exc, self._delay = article, exc, delay
        self.calls: list[tuple[str, str]] = []

    async def find(self, headline: str, channel: str, preferred=()):
        self.calls.append((headline, channel, tuple(preferred)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self.exc:
            raise self.exc
        return self.article


ARTICLE = TvNewsArticle(
    title="維他命添色素影響智力？ - TVBS新聞", url="https://news.tvbs.com.tw/a/1", score=1.0
)


class _Memory:
    def __init__(self, preferred=()):
        self._preferred = tuple(preferred)
        self.remembered: list[tuple[str | None, str]] = []

    async def preferred(self, user_id):
        return self._preferred

    async def remember(self, user_id, channel):
        self.remembered.append((user_id, channel))


@pytest.fixture(autouse=True)
def _reset():
    yield
    claim_tools.configure_claim_tool(None)
    tv_news_tools.configure_tv_news_tool(None, None)


def _buttons(payload: dict) -> list[str]:
    footer = payload["contents"].get("footer") or {"contents": []}
    return [box["contents"][0]["text"] for box in footer["contents"]]


@pytest.mark.asyncio
async def test_找到原報導時卡片多一顆新聞原文且排在最前面():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(ARTICLE))

    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"}))
    labels = _buttons(payload)
    assert labels[0].startswith("看新聞原文")
    assert "查看查核報告 →" in labels
    assert "https://news.tvbs.com.tw/a/1" in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_影片的按鈕字樣是新聞影片():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(
        _Finder(TvNewsArticle(title="TVBS新聞", url="https://www.youtube.com/watch?v=x", score=1.0, is_video=True))
    )
    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"}))
    assert _buttons(payload)[0].startswith("看新聞影片")


@pytest.mark.asyncio
async def test_找不到就只有判定卡():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(None))
    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"}))
    assert all(not label.startswith("看新聞") for label in _buttons(payload))


@pytest.mark.asyncio
async def test_找新聞爆炸不影響判定卡():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(exc=RuntimeError("firecrawl 掛了")))
    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"}))
    assert payload["contents"]["header"]["contents"][0]["text"] == "錯誤"


@pytest.mark.asyncio
async def test_查核與找新聞並行():
    """串起來就是把搜尋的時間直接加在長輩的等待上。"""
    claim_tools.configure_claim_tool(_Service(delay=0.05))
    tv_news_tools.configure_tv_news_tool(_Finder(ARTICLE, delay=0.05))
    started = asyncio.get_event_loop().time()
    await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"})
    assert asyncio.get_event_loop().time() - started < 0.09


@pytest.mark.asyncio
async def test_查核送的是標題原文():
    service = _Service()
    claim_tools.configure_claim_tool(service)
    finder = _Finder(ARTICLE)
    tv_news_tools.configure_tv_news_tool(finder)
    await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"})
    assert service.queries == [HEADLINE]
    assert finder.calls == [(HEADLINE, "TVBS", ())]


@pytest.mark.asyncio
async def test_沒有查核服務時不提供這支工具():
    claim_tools.configure_claim_tool(None)
    assert tv_news_tools.is_tv_news_tool_configured() is False
    claim_tools.configure_claim_tool(_Service())
    assert tv_news_tools.is_tv_news_tool_configured() is True


def _quick_reply_texts(payload: dict) -> list[str]:
    return [item["action"]["text"] for item in payload.get("quickReply", {}).get("items", [])]


@pytest.mark.asyncio
async def test_找不到又認不出台別時回問是哪一台_常看的排前面():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(None), _Memory(["民視", "公視"]))

    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": ""}))
    texts = _quick_reply_texts(payload)
    assert texts[:2] == ["這則新聞是民視", "這則新聞是公視"]
    assert len(texts) == 5  # 不足的用常見台別補滿，不是整排十三台
    assert "哪一台" in payload["followUpText"]


@pytest.mark.asyncio
async def test_找到了就不回問():
    """已經解決的問題不要再丟回去問長輩。"""
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(ARTICLE), _Memory())
    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": ""}))
    assert "quickReply" not in payload


@pytest.mark.asyncio
async def test_台別認得出來就不回問_而且記起來():
    claim_tools.configure_claim_tool(_Service())
    memory = _Memory()
    tv_news_tools.configure_tv_news_tool(_Finder(None), memory)
    payload = json.loads(await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": "TVBS"}))
    assert "quickReply" not in payload
    assert [ch for _, ch in memory.remembered] == ["TVBS"]


@pytest.mark.asyncio
async def test_猜不出來的台別不記():
    """記錯一次會一路影響之後每一次比對的門檻。"""
    claim_tools.configure_claim_tool(_Service())
    memory = _Memory()
    tv_news_tools.configure_tv_news_tool(_Finder(None), memory)
    await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": ""})
    assert memory.remembered == []


@pytest.mark.asyncio
async def test_常看的台別會傳給搜尋():
    claim_tools.configure_claim_tool(_Service())
    finder = _Finder(None)
    tv_news_tools.configure_tv_news_tool(finder, _Memory(["三立"]))
    await tv_news_tools.verify_tv_news.ainvoke({"headline": HEADLINE, "channel": ""})
    assert finder.calls == [(HEADLINE, "", ("三立",))]


@pytest.mark.asyncio
async def test_使用者回答台別後只找連結不重做查核():
    service = _Service()
    claim_tools.configure_claim_tool(service)
    memory = _Memory()
    tv_news_tools.configure_tv_news_tool(_Finder(ARTICLE), memory)

    reply = await tv_news_tools.find_tv_news_article.ainvoke({"headline": HEADLINE, "channel": "TVBS"})
    assert "https://news.tvbs.com.tw/a/1" in reply
    assert service.queries == []  # 判定上一則訊息已經給過了
    assert [ch for _, ch in memory.remembered] == ["TVBS"]


@pytest.mark.asyncio
async def test_回答台別後仍找不到_說清楚是電視台沒放上網():
    claim_tools.configure_claim_tool(_Service())
    tv_news_tools.configure_tv_news_tool(_Finder(None), _Memory())
    reply = await tv_news_tools.find_tv_news_article.ainvoke({"headline": HEADLINE, "channel": "民視"})
    assert "找不到" in reply and "沒有把它放上網站" in reply
