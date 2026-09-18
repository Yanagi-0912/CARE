"""從電視新聞畫面的標題，找回那則新聞的原始報導。

長輩拍下來的畫面只有字，沒有網址。查核卡上的來源是查核報告或知識庫文章，
回答不了「我看到的那則新聞本身在哪裡」——這支服務補的就是那個連結。

**寧可不附，也不附錯的。** 附上別則新聞比沒有連結更糟：長輩會以為那就是他
看到的報導，而我們沒有任何機制讓他發現接錯了。所以兩道條件都要過：

1. 出處必須是該台自己的新聞網站（`CHANNEL_DOMAINS`），或該台自己上傳的
   YouTube 影片（網域是 YouTube 且標題裡有台名）。
2. 螢幕上的標題要幾乎整句出現在文章標題裡（`MATCH_THRESHOLD`）。

抽不到台別、或那台不在對照表裡，就完全不查——沒有出處可比，只剩標題相似度，
擋不住同名事件的別則報導。

**刻意不收轉載站**（Yahoo、LINE TODAY、Facebook）與別家媒體：2026-09-18 的
實測裡，TVBS 那則「38歲洗腎男路倒不治」在中時有一篇 0.83 分的報導，那是別家
記者寫的同一件事，不是長輩看到的那一則；Yahoo／LINE TODAY 雖然多半是原文
轉載，但從搜尋結果分不出「授權轉載」與「別家報導同一事件」，一律不收。

**命中率**：35 組真實標題中 10 組找得到（自家網站 6、官方 YouTube 影片 4），
10 筆都是同一則新聞；其餘 25 組不附連結。電視台不是每則新聞都上網，這是這個
做法的上限，不是門檻調得太嚴。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional, Protocol, Sequence
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# 十三家電視台的新聞網站。值是網域後綴，比對時只看「結尾是不是它」，
# 所以 news.tvbs.com.tw 這種子網域一併涵蓋。
#
# 清單與 CARE-n8n 影像節點認得的台別一致（`mutimedia process.json` 的
# TV_CHANNELS）：那邊認不出來的台別根本不會下送，這邊多列也用不到。
CHANNEL_DOMAINS: dict[str, tuple[str, ...]] = {
    "台視": ("ttv.com.tw",),
    "中視": ("ctv.com.tw",),
    "華視": ("cts.com.tw",),
    "民視": ("ftvnews.com.tw", "ftv.com.tw"),
    "公視": ("pts.org.tw",),
    "TVBS": ("tvbs.com.tw",),
    "三立": ("setn.com",),
    "東森": ("ebc.net.tw", "ettoday.net"),
    "中天": ("ctinews.com", "ctitv.com.tw"),
    "年代": ("eracom.com.tw",),
    "壹電視": ("nexttv.com.tw",),
    "非凡": ("ustv.com.tw",),
    "寰宇": ("globalnewstv.com.tw",),
}

# 標題比對前要丟掉的東西：電視標題用的分隔符（‧·．.、!?）、引號、空白，
# 以及網站標題常見的「｜中視新聞網」這類後綴分隔符。留著它們會讓同一句話
# 因為標點寫法不同而比不出來。
_NOISE_RE = re.compile(r"[\s　!！?？.,，、。：:；;\"'“”‘’「」『』（）()\[\]【】\-—–_|｜/／‧·・．]+")

_QUERY_NOISE_RE = re.compile(r"[\"'“”‘’「」『』]+")


# 該台自己上傳的影片。搜尋結果只有標題與網址、沒有頻道名稱，所以用標題裡的
# 台名當歸屬判準——電視台上傳時幾乎都會在標題帶上自己的名字（實測命中的影片
# 全部都有）。這條規則把命中率從 6/35 拉到 10/35。
_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")

CHANNEL_ALIASES: dict[str, tuple[str, ...]] = {
    "台視": ("台視",),
    "中視": ("中視",),
    "華視": ("華視",),
    "民視": ("民視",),
    "公視": ("公視", "公共電視"),
    "TVBS": ("TVBS",),
    "三立": ("三立", "SETN"),
    "東森": ("東森", "ETtoday"),
    "中天": ("中天",),
    "年代": ("年代",),
    "壹電視": ("壹電視", "壹起"),
    "非凡": ("非凡",),
    "寰宇": ("寰宇",),
}


@dataclass(frozen=True)
class TvNewsArticle:
    """找到的原始報導。`score` 是標題吻合度，留著是為了讓 log 看得出邊界情況。

    `is_video` 決定按鈕要寫「新聞原文」還是「新聞影片」——點下去是影片卻寫
    原文，使用者會以為自己點錯。
    """

    title: str
    url: str
    score: float
    is_video: bool = False


class SearchClient(Protocol):
    async def search(
        self, query: str, *, limit: int = 5, include_domains: Sequence[str] | None = None
    ) -> list[Any]: ...


def normalize(text: str) -> str:
    return _NOISE_RE.sub("", text or "")


def title_match_score(headline: str, title: str) -> float:
    """螢幕標題有多少比例出現在文章標題裡（0–1）。

    用「涵蓋率」而不是 `SequenceMatcher.ratio()`：網站標題往往比螢幕標題長
    （多了副標、台名後綴），兩邊長度差很多時 ratio 會被稀釋，同一則新聞也只
    拿到 0.6 上下。這裡要問的是「螢幕上那句話是不是都在文章標題裡」，分母
    因此固定是螢幕標題。
    """
    left, right = normalize(headline), normalize(title)
    if not left or not right:
        return 0.0
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(left)


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, domains: Sequence[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_channel_domain(url: str, channel: str) -> bool:
    domains = CHANNEL_DOMAINS.get(channel)
    if not domains:
        return False
    return _host_matches(domain_of(url), domains)


def is_channel_video(url: str, title: str, channel: str) -> bool:
    """該台自己上傳的 YouTube 影片：網域是 YouTube 且標題裡有台名。"""
    if not _host_matches(domain_of(url), _YOUTUBE_DOMAINS):
        return False
    aliases = CHANNEL_ALIASES.get(channel, ())
    lowered = (title or "").lower()
    return any(alias.lower() in lowered for alias in aliases)


def build_query(headline: str, channel: str) -> str:
    """搜尋字串：台名＋標題。引號要拿掉，它在搜尋引擎裡是「完全相符」運算子。"""
    return f"{channel} {_QUERY_NOISE_RE.sub(' ', headline)}".strip()


class TvNewsArticleFinder:
    """`MATCH_THRESHOLD` 的由來見 `scripts/tv_news_article_eval.py` 的實測表。"""

    # 2026-09-18 實測（35 組真實螢幕標題，scripts/tv_news_article_eval.py）：
    # 0.75 以上共 7 筆，全部是同一則新聞；最高的錯誤候選是 0.63（公視「成人健檢
    # 送800點」對到同一主題的另一篇報導），所以門檻壓在兩者中間偏高處。降到 0.5
    # 會多收 3 筆，其中至少 1 筆是同事件的別篇。
    MATCH_THRESHOLD = 0.75
    SEARCH_LIMIT = 5

    def __init__(
        self,
        search_client: SearchClient | None,
        *,
        threshold: float | None = None,
    ) -> None:
        self._search = search_client
        self._threshold = self.MATCH_THRESHOLD if threshold is None else threshold

    async def find(self, headline: str, channel: str) -> Optional[TvNewsArticle]:
        """找不到、認不得台別、或搜尋失敗都回 None——這是加值，不能擋住查核。"""
        if not self._search or not headline or channel not in CHANNEL_DOMAINS:
            return None
        # 不限定網域搜一次就好：限定網域的結果是這一份的子集（實測 7 筆自家
        # 命中在開放搜尋裡也都在前五），多搜一次只是多花一次額度。
        try:
            hits = await self._search(
                build_query(headline, channel), limit=self.SEARCH_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 - 搜不到就不附連結，不影響判定卡
            logger.warning("stage=tv_news_article 搜尋失敗：%s", type(exc).__name__)
            return None

        best: Optional[TvNewsArticle] = None
        for hit in hits or []:
            url = getattr(hit, "url", "") or ""
            title = getattr(hit, "title", "") or ""
            is_own_site = is_channel_domain(url, channel)
            is_video = is_channel_video(url, title, channel)
            if not is_own_site and not is_video:
                continue
            score = title_match_score(headline, title)
            if score < self._threshold:
                continue
            # 同分時文章優先於影片：長輩在 LINE 裡開文章比開影片省流量，也讀得快。
            better = best is None or score > best.score or (
                score == best.score and best.is_video and not is_video
            )
            if better:
                best = TvNewsArticle(
                    title=title, url=url, score=score, is_video=is_video
                )
        log_score = round(best.score, 3) if best else None
        logger.info(
            "stage=tv_news_article channel=%s hits=%d matched=%s score=%s",
            channel,
            len(hits or []),
            bool(best),
            log_score,
        )
        return best
